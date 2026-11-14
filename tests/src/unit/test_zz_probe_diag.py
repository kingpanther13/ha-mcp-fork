import logging


async def test_probe_diag(read_only_client, core_client, caplog):
    caplog.set_level(logging.DEBUG)
    ro = await read_only_client._current_user()
    ad = await core_client._current_user()
    print("RO_USER", ro)
    print("ADMIN_USER", ad)
    print("LOGTAIL", caplog.text[-2500:])
    assert False
