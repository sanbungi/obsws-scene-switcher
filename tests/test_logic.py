from switcher import merge_config, normalize, resolve_scene


def test_normalize_strips_and_lowercases():
    assert normalize("  Firefox  ") == "firefox"


def test_normalize_collapses_spaces_and_symbols():
    assert normalize("Org.KDE.Konsole!!") == "org.kde.konsole"
    assert normalize("my  app") == "my-app"


def test_normalize_empty():
    assert normalize("   ") == ""
    assert normalize("@@@") == ""


def test_resolve_mapped_window():
    mappings = {"firefox": "Firefox"}
    assert resolve_scene("Firefox", mappings, "Safe") == "Firefox"


def test_resolve_unmapped_uses_safe():
    mappings = {"firefox": "Firefox"}
    assert resolve_scene("org.kde.dolphin", mappings, "Safe") == "Safe"


def test_resolve_repeated_identifier_uses_concise_mapping():
    mappings = {"firefox": "Firefox"}
    assert resolve_scene("firefox_firefox", mappings, "Safe") == "Firefox"


def test_resolve_full_identifier_overrides_concise_mapping():
    mappings = {"firefox": "Firefox", "firefox_firefox": "Private Firefox"}
    assert resolve_scene("firefox_firefox", mappings, "Safe") == "Private Firefox"


def test_resolve_partial_match_obeys_component_boundaries():
    mappings = {"firefox": "Firefox", "code": "Code"}
    assert resolve_scene("firefox_firefox", mappings, "Safe") == "Firefox"
    assert resolve_scene("my-code-window", mappings, "Safe") == "Code"
    assert resolve_scene("codec", mappings, "Safe") == "Safe"


def test_resolve_partial_match_can_be_disabled():
    assert resolve_scene(
        "firefox_firefox", {"firefox": "Firefox"}, "Safe",
        partial_match=False,
    ) == "Safe"


def test_resolve_can_preserve_case():
    mappings = {"Firefox": "Browser"}
    assert resolve_scene(
        "Firefox", mappings, "Safe", case_insensitive=False,
    ) == "Browser"
    assert resolve_scene(
        "firefox", mappings, "Safe", case_insensitive=False,
    ) == "Safe"


def test_resolve_empty_is_none_key():
    mappings = {"none": "Idle"}
    assert resolve_scene("", mappings, "Safe") == "Idle"
    assert resolve_scene("   ", mappings, "Safe") == "Idle"


def test_merge_config_defaults():
    cfg = merge_config(None)
    assert cfg["obs_host"] == "127.0.0.1"
    assert cfg["obs_port"] == 4455
    assert cfg["safe_scene"] == "Safe"
    assert "firefox" in cfg["mappings"]


def test_merge_config_overrides_and_normalizes_mappings():
    cfg = merge_config({
        "obs_host": "10.0.0.2",
        "obs_port": "4456",
        "safe_scene": "BRB",
        "mappings": {
            " Firefox ": " Browser ",
        },
    })
    assert cfg["obs_host"] == "10.0.0.2"
    assert cfg["obs_port"] == 4456
    assert cfg["safe_scene"] == "BRB"
    assert cfg["mappings"] == {"firefox": "Browser"}


def test_merge_config_matching_options_are_saved_and_apply_to_keys():
    cfg = merge_config({
        "matching": {"case_insensitive": False, "partial_match": False},
        "mappings": {"Firefox": "Browser"},
    })
    assert cfg["matching"] == {
        "case_insensitive": False,
        "partial_match": False,
    }
    assert cfg["mappings"] == {"Firefox": "Browser"}


def test_merge_config_bad_port_and_empty_safe():
    cfg = merge_config({"obs_port": "nope", "safe_scene": "  "})
    assert cfg["obs_port"] == 4455
    assert cfg["safe_scene"] == "Safe"


def test_merge_config_invalid_mappings_falls_back():
    cfg = merge_config({"mappings": "oops"})
    assert cfg["mappings"]["firefox"] == "Firefox"
