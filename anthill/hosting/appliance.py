"""Mac (Apple Silicon) always-on appliance helpers.

A Mac Mini / Studio runs *stock macOS* configured as a headless, always-on org backend -
it is NOT a separate "server OS" (Apple discontinued macOS Server in 2022). The one hard
constraint: Ollama's GPU (Metal) is only available inside a logged-in user (Aqua) session,
so the appliance runs as a **LaunchAgent** in an auto-logged-in session, never a system
LaunchDaemon. See engineering-plans/MAC_MINI_ON_PREM.md.

Everything here is pure / boundary-injected so it unit-tests without touching the host.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import xml.sax.saxutils as _xml
from collections.abc import Callable
from dataclasses import dataclass

LAUNCHAGENT_LABEL = "org.onehill.anthill"

# The always-on power settings the installer applies (and the Settings page recommends).
# Each is (command, why). pmset needs sudo; surfaced so an admin can paste them.
PMSET_SETTINGS: list[tuple[str, str]] = [
    ("sudo pmset -a sleep 0 disablesleep 1", "Never sleep - the box must stay up to serve."),
    ("sudo pmset -a autorestart 1", "Restart automatically after a power failure."),
    ("sudo pmset -a disksleep 0", "Keep the disk awake so the first request never stalls."),
    ("sudo pmset -a womp 1", "Wake for network access."),
]


# --- process boundary (injectable for tests) ------------------------------------------


def _default_runner(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _run(cmd: list[str], *, runner: Callable[[list[str]], str] | None = None) -> str:
    return (runner or _default_runner)(cmd)


# --- network --------------------------------------------------------------------------


def primary_lan_ip(*, resolve: Callable[[], str] | None = None) -> str:
    """Best-effort primary LAN IPv4 (the egress interface's address).

    Opens a UDP socket toward a non-routable TEST-NET address - no packet is sent, it only
    makes the OS choose the outbound interface so getsockname() reports the real LAN IP.
    Returns "" when it cannot be determined (e.g. no network).
    """
    if resolve is not None:
        return resolve()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, RFC 5737 - never actually routed
            return str(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        return ""


def lan_url(port: int, *, ip: str | None = None) -> str:
    ip = primary_lan_ip() if ip is None else ip
    return f"http://{ip}:{port}" if ip else ""


# --- GPU / session detection ----------------------------------------------------------


@dataclass
class Gpu:
    backend: str  # "metal" | "cpu"
    detail: str
    accelerated: bool


def is_apple_silicon(*, system: str | None = None, machine: str | None = None) -> bool:
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    return system == "Darwin" and machine == "arm64"


def session_kind(*, runner: Callable[[list[str]], str] | None = None) -> str:
    """The current launchd domain name. 'Aqua' = a logged-in GUI session (Metal works);
    'Background'/'System'/'StandardIO' = no GUI session (a daemon or ssh - no GPU)."""
    return _run(["launchctl", "managername"], runner=runner).strip()


def gpu_backend(
    *,
    apple: bool | None = None,
    session: str | None = None,
    runner: Callable[[list[str]], str] | None = None,
) -> Gpu:
    """Decide whether the served model gets the Apple GPU (Metal) or falls back to CPU.

    Metal requires a logged-in (Aqua) session. Running as a system daemon or over ssh has
    no session -> CPU, which is why the appliance must auto-login + run as a LaunchAgent.
    """
    apple = is_apple_silicon() if apple is None else apple
    if not apple:
        return Gpu("cpu", "Not Apple Silicon - no Metal GPU on this host.", False)
    sess = session_kind(runner=runner) if session is None else session
    if sess == "Aqua":
        return Gpu("metal", "Apple GPU (Metal) via a logged-in session.", True)
    return Gpu(
        "cpu",
        f"No GUI session ({sess or 'unknown'}) - Metal is unavailable. Enable auto-login and "
        "run Anthill as a LaunchAgent so Ollama gets the GPU.",
        False,
    )


# --- LaunchAgent ----------------------------------------------------------------------


def _esc(value: object) -> str:
    return _xml.escape(str(value))


def web_server_args(
    *, anthill_bin: str = "anthill", host: str = "0.0.0.0", port: int = 8000
) -> list[str]:
    """The argv for the headless dashboard server the LaunchAgent runs."""
    return [anthill_bin, "web", "--host", host, "--port", str(port)]


def launchagent_plist(
    *,
    program_args: list[str],
    label: str = LAUNCHAGENT_LABEL,
    stdout_path: str = "",
    stderr_path: str = "",
    working_dir: str = "",
    env: dict[str, str] | None = None,
    run_at_load: bool = True,
    keep_alive: bool = True,
) -> str:
    """A macOS LaunchAgent property list that starts `program_args` at login and restarts
    it on crash (KeepAlive). Goes in ~/Library/LaunchAgents/<label>.plist."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        "<dict>",
        "  <key>Label</key>",
        f"  <string>{_esc(label)}</string>",
        "  <key>ProgramArguments</key>",
        "  <array>",
    ]
    lines += [f"    <string>{_esc(a)}</string>" for a in program_args]
    lines.append("  </array>")
    if working_dir:
        lines += ["  <key>WorkingDirectory</key>", f"  <string>{_esc(working_dir)}</string>"]
    if env:
        lines.append("  <key>EnvironmentVariables</key>")
        lines.append("  <dict>")
        for k, v in env.items():
            lines += [f"    <key>{_esc(k)}</key>", f"    <string>{_esc(v)}</string>"]
        lines.append("  </dict>")
    lines += ["  <key>RunAtLoad</key>", f"  <{'true' if run_at_load else 'false'}/>"]
    lines += ["  <key>KeepAlive</key>", f"  <{'true' if keep_alive else 'false'}/>"]
    if stdout_path:
        lines += ["  <key>StandardOutPath</key>", f"  <string>{_esc(stdout_path)}</string>"]
    if stderr_path:
        lines += ["  <key>StandardErrorPath</key>", f"  <string>{_esc(stderr_path)}</string>"]
    # Interactive so an always-on server is not throttled like a background batch job.
    lines += ["  <key>ProcessType</key>", "  <string>Interactive</string>"]
    lines += ["</dict>", "</plist>", ""]
    return "\n".join(lines)


def launchagent_path(label: str = LAUNCHAGENT_LABEL, *, home: str = "") -> str:
    home = home or os.path.expanduser("~")
    return os.path.join(home, "Library", "LaunchAgents", f"{label}.plist")


def is_service_loaded(
    label: str = LAUNCHAGENT_LABEL, *, runner: Callable[[list[str]], str] | None = None
) -> bool:
    """Whether `label` is currently loaded in launchd (`launchctl list`)."""
    out = _run(["launchctl", "list"], runner=runner)
    for line in out.splitlines():
        line = line.strip()
        if line and line.split()[-1] == label:  # output is PID<TAB>Status<TAB>Label
            return True
    return False


# --- status assembly ------------------------------------------------------------------


@dataclass
class ApplianceStatus:
    is_apple_silicon: bool
    gpu: Gpu
    lan_url: str
    tunnel_url: str
    model: str
    service_loaded: bool
    service_label: str
    plist_path: str


def appliance_status(
    *,
    port: int,
    model: str = "",
    tunnel_url: str = "",
    label: str = LAUNCHAGENT_LABEL,
    home: str = "",
    lan_ip: str | None = None,
    apple: bool | None = None,
    session: str | None = None,
    runner: Callable[[list[str]], str] | None = None,
) -> ApplianceStatus:
    apple = is_apple_silicon() if apple is None else apple
    return ApplianceStatus(
        is_apple_silicon=apple,
        gpu=gpu_backend(apple=apple, session=session, runner=runner),
        lan_url=lan_url(port, ip=lan_ip),
        tunnel_url=tunnel_url,
        model=model,
        service_loaded=is_service_loaded(label, runner=runner),
        service_label=label,
        plist_path=launchagent_path(label, home=home),
    )


# --- hardware sizing ------------------------------------------------------------------


def total_memory_gb(*, runner: Callable[[list[str]], str] | None = None) -> float:
    """Physical RAM in GB. Uses `sysctl hw.memsize` on macOS, falling back to sysconf."""
    out = _run(["sysctl", "-n", "hw.memsize"], runner=runner).strip()
    if out.isdigit():
        return int(out) / (1024**3)
    try:  # POSIX fallback (also works on Linux)
        return (os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")) / (1024**3)
    except (ValueError, OSError, AttributeError):
        return 0.0


def recommended_ollama_tag(mem_gb: float, *, default: str = "qwen3.5:0.8b") -> str:
    """The largest catalog model that fits this Mac's unified memory, as an Ollama tag."""
    from . import sizing

    # Capacity question ("largest that fits this Mac"): size the single-user envelope (concurrency 1),
    # against the model's resident footprint (issue #490).
    rec = sizing.recommend(mem_gb, kind="apple", concurrency=1).recommended if mem_gb > 0 else None
    return (rec.ollama_tag if rec and rec.ollama_tag else "") or default


# --- installer (side effects are boundary-injected so it unit-tests offline) -----------


def _default_writer(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def _default_puller(tag: str) -> bool:
    ollama = os.path.expanduser("~/bin/ollama")
    if not os.path.exists(ollama):
        ollama = "ollama"
    try:
        return subprocess.run([ollama, "pull", tag], timeout=3600).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def write_launchagent(
    plist_text: str, *, path: str, writer: Callable[[str, str], None] | None = None
) -> str:
    (writer or _default_writer)(path, plist_text)
    return path


def load_launchagent(path: str, *, runner: Callable[[list[str]], str] | None = None) -> None:
    """(Re)load the LaunchAgent: unload an old copy if present, then load the new one."""
    _run(["launchctl", "unload", path], runner=runner)  # ignore "not loaded"
    _run(["launchctl", "load", path], runner=runner)


def unload_launchagent(path: str, *, runner: Callable[[list[str]], str] | None = None) -> None:
    _run(["launchctl", "unload", path], runner=runner)


def apply_power_settings(
    *, runner: Callable[[list[str]], int] | None = None
) -> list[tuple[str, bool]]:
    """Apply the always-on `pmset` settings (needs sudo). Returns (command, ok) per setting.
    The default runner inherits stdio so an interactive sudo prompt works in a terminal."""

    def _default(cmd: list[str]) -> int:
        try:
            return subprocess.run(cmd).returncode
        except (OSError, subprocess.SubprocessError):
            return 1

    run = runner or _default
    results: list[tuple[str, bool]] = []
    for cmd, _why in PMSET_SETTINGS:
        results.append((cmd, run(cmd.split()) == 0))
    return results


@dataclass
class InstallResult:
    plist_path: str
    model: str
    pulled: bool
    lan_url: str
    gpu: Gpu
    power_applied: bool
    next_steps: list[str]


def install_appliance(
    *,
    port: int = 8000,
    model: str = "",
    pull: bool = True,
    apply_power: bool = False,
    label: str = LAUNCHAGENT_LABEL,
    home: str = "",
    log_dir: str = "",
    mem_gb: float | None = None,
    lan_ip: str | None = None,
    apple: bool | None = None,
    session: str | None = None,
    writer: Callable[[str, str], None] | None = None,
    runner: Callable[[list[str]], str] | None = None,
    puller: Callable[[str], bool] | None = None,
    power_runner: Callable[[list[str]], int] | None = None,
) -> InstallResult:
    """Install Anthill as an always-on LaunchAgent on this Mac.

    Writes + loads the LaunchAgent, pulls the (sized or given) model, optionally applies the
    pmset power settings, and returns what is left for the admin to do by hand (auto-login,
    FileVault). Every side effect is injectable, so the whole thing tests without a Mac.
    """
    home = home or os.path.expanduser("~")
    mem = total_memory_gb(runner=runner) if mem_gb is None else mem_gb
    model = model or recommended_ollama_tag(mem)
    log_dir = log_dir or os.path.join(home, "Library", "Logs", "Anthill")

    plist = launchagent_plist(
        program_args=web_server_args(port=port),
        label=label,
        stdout_path=os.path.join(log_dir, "anthill.out.log"),
        stderr_path=os.path.join(log_dir, "anthill.err.log"),
        env={"ANTHILL_MODEL": model} if model else None,
    )
    path = launchagent_path(label, home=home)
    write_launchagent(plist, path=path, writer=writer)
    load_launchagent(path, runner=runner)

    pulled = bool(pull and model and (puller or _default_puller)(model))
    power_applied = bool(
        apply_power and all(ok for _c, ok in apply_power_settings(runner=power_runner))
    )

    gpu = gpu_backend(apple=apple, session=session, runner=runner)
    steps: list[str] = []
    if not power_applied:
        steps.append(
            "Set the power settings (need sudo): " + "; ".join(cmd for cmd, _why in PMSET_SETTINGS)
        )
    steps.append(
        "Enable auto-login (System Settings -> Users & Groups -> Login Options) for the user this "
        "runs as - a logged-in session is what gives Ollama the GPU."
    )
    steps.append(
        "FileVault blocks unattended auto-login; either leave it off (physical security) or set up "
        "fdesetup automatic unlock."
    )
    if not gpu.accelerated and apple is not False:
        steps.append(f"GPU not active yet: {gpu.detail}")

    return InstallResult(
        plist_path=path,
        model=model,
        pulled=pulled,
        lan_url=lan_url(port, ip=lan_ip),
        gpu=gpu,
        power_applied=power_applied,
        next_steps=steps,
    )
