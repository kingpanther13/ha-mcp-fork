

@pytest.fixture(autouse=True)
async def occupy_any_released_target_port(monkeypatch, request):
    """Proof-only competitor: reproduce the CI port reuse deterministically."""
    import errno
    from urllib.parse import urlsplit

    from aiohttp import web

    original_write = request.module.write_proxy_config
    original_setup = request.module.set_up_proxy
    target = {}
    app = web.Application()

    async def unrelated_server(request):
        return web.Response(status=404)

    app.router.add_route("*", "/{path:.*}", unrelated_server)
    runner = web.AppRunner(app)
    await runner.setup()

    def capture_target(fs_root, variant, config):
        target["url"] = config["target_url"]
        return original_write(fs_root, variant, config)

    async def start_after_port_reuse(hass, variant):
        parsed = urlsplit(target["url"])
        site = web.TCPSite(runner, parsed.hostname, parsed.port)
        try:
            await site.start()
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            print("PROOF: target port remained reserved")
        else:
            print("PROOF: released target port was reused by an unrelated server")
        return await original_setup(hass, variant)

    monkeypatch.setattr(request.module, "write_proxy_config", capture_target)
    monkeypatch.setattr(request.module, "set_up_proxy", start_after_port_reuse)
    try:
        yield
    finally:
        await runner.cleanup()
