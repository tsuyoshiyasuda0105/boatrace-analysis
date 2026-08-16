from scripts import incident


def test_cli_lists_and_resolves(monkeypatch, capsys):
    monkeypatch.setattr(
        incident,
        "list_incidents",
        lambda **kwargs: [{"incident_id": "inc-1", "app_name": kwargs["app_name"]}],
    )
    assert incident.main(["list", "--app", "shared-app", "--status", "open", "--limit", "7"]) == 0
    assert '"incident_id": "inc-1"' in capsys.readouterr().out

    calls = []
    monkeypatch.setattr(
        incident,
        "resolve_incident",
        lambda key, **kwargs: calls.append((key, kwargs)) or True,
    )
    assert incident.main(["resolve", "inc-1", "--by", "rin", "--note", "fixed"]) == 0
    assert calls == [("inc-1", {"handled_by": "rin", "response_note": "fixed", "status": "resolved"})]
