"""#661 Tier 5: TCP-only reachability checks (anthill.hosting.cluster). llama.cpp's rpc-server protocol
is not HTTP, so these can only ever confirm "something is listening on this port," never "the
rpc-server is healthy" - see the module's own docstring."""

import socket

from anthill.hosting.cluster import tcp_reachable, worker_reachability


def test_tcp_reachable_true_for_a_real_listening_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert tcp_reachable("127.0.0.1", port, timeout=1.0) is True
    finally:
        srv.close()


def test_tcp_reachable_false_for_a_closed_port():
    # Bind then immediately close, so we know the port is free and nothing is listening.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert tcp_reachable("127.0.0.1", port, timeout=1.0) is False


def test_tcp_reachable_false_for_blank_host_or_port():
    assert tcp_reachable("", 1234) is False
    assert tcp_reachable("127.0.0.1", 0) is False


def test_tcp_reachable_never_raises_on_a_bad_host():
    assert tcp_reachable("this-host-does-not-resolve.invalid", 1234, timeout=0.5) is False


def test_worker_reachability_uses_injected_check_and_preserves_fields():
    workers = [
        {"label": "a", "host": "host-a", "port": "1", "mem_gb": "64"},
        {"label": "b", "host": "host-b", "port": "2", "mem_gb": "32"},
    ]
    fake = lambda h, p: h == "host-a"  # noqa: E731
    out = worker_reachability(workers, check=fake)
    assert out[0]["reachable"] is True
    assert out[1]["reachable"] is False
    # original fields preserved
    assert out[0]["label"] == "a" and out[0]["mem_gb"] == "64"
    assert out[1]["label"] == "b" and out[1]["mem_gb"] == "32"


def test_worker_reachability_missing_host_or_port_is_unreachable_without_calling_check():
    calls = []
    fake = lambda h, p: calls.append((h, p)) or True  # noqa: E731
    out = worker_reachability([{"host": "", "port": "1"}, {"host": "h", "port": ""}], check=fake)
    assert out[0]["reachable"] is False
    assert out[1]["reachable"] is False
    assert calls == []  # never even called the check for an incomplete worker


def test_worker_reachability_non_numeric_port_is_unreachable():
    out = worker_reachability([{"host": "h", "port": "not-a-number"}], check=lambda h, p: True)
    assert out[0]["reachable"] is False
