"""Saving the org model-server form must not knock a live backend back to 'planned'.

Regression: re-saving Settings -> Organization reset org_backend_status to 'planned' even when the
provisioned RunPod pod was still up, which made org_available() False and silently disabled creating
new Org chats (the user kept landing in Solo). A live backend now survives an unchanged re-save.
"""

from anthill.web.app import _org_status_after_save


def test_no_plan_is_unconfigured():
    # No provider+model -> unconfigured, whatever the prior status.
    assert (
        _org_status_after_save("provisioned", has_plan=False, selection_changed=False)
        == "unconfigured"
    )
    assert _org_status_after_save("", has_plan=False, selection_changed=True) == "unconfigured"


def test_live_backend_survives_unchanged_resave():
    # The core regression: an unchanged re-save keeps the live backend, so Org chat stays available.
    assert (
        _org_status_after_save("provisioned", has_plan=True, selection_changed=False)
        == "provisioned"
    )
    assert (
        _org_status_after_save("validated", has_plan=True, selection_changed=False) == "validated"
    )
    # Case/whitespace tolerant.
    assert (
        _org_status_after_save("  Provisioned ", has_plan=True, selection_changed=False)
        == "provisioned"
    )


def test_changed_selection_replans_even_if_live():
    # A different provider/model/size needs a different pod -> back to planned.
    assert _org_status_after_save("provisioned", has_plan=True, selection_changed=True) == "planned"
    assert _org_status_after_save("validated", has_plan=True, selection_changed=True) == "planned"


def test_not_yet_live_stays_planned():
    # planned/provisioning/error/empty are not "live", so they (re)plan regardless of change.
    for prev in ("", "planned", "provisioning", "error", "unconfigured"):
        assert _org_status_after_save(prev, has_plan=True, selection_changed=False) == "planned"
        assert _org_status_after_save(prev, has_plan=True, selection_changed=True) == "planned"
