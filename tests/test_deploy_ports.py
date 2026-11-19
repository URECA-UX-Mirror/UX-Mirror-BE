"""배포 스크립트의 쇼핑몰 목록이 등록된 사이트와 어긋나지 않는지 (#126 리뷰).

포트·사이트가 run_shops.sh `SHOPS`, lib.sh `SHOP_PORTS`, env.server.example, config/site_instances.json
네 곳에 따로 있어서, 한 곳만 바꾸면 서버에서 사이트가 안 뜨거나 점검이 그 사이트를 빼먹는다.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _shops():
    """run_shops.sh SHOPS → {port: 폴더}"""
    specs = re.search(r'^SHOPS="([^"]+)"', _read("deploy", "run_shops.sh"), re.M).group(1).split()
    return {spec.split(":")[1]: spec.split(":")[0] for spec in specs}


def test_every_served_shop_folder_exists():
    for port, folder in _shops().items():
        assert os.path.isfile(os.path.join(ROOT, "shop", folder, "server.py")), (port, folder)


def test_health_check_ports_match_served_shops():
    ports = re.search(r'SHOP_PORTS="\$\{UXM_SHOP_PORTS:-([^}]+)\}"', _read("deploy", "lib.sh")).group(1).split()
    assert sorted(ports) == sorted(_shops())


def test_registered_and_configured_sites_are_served():
    registered = {re.search(r":(\d+)$", i["baseUrl"]).group(1)
                  for i in json.loads(_read("config", "site_instances.json"))["instances"]}
    configured = set(re.findall(r"^SHOP_\w+_URL=http://127\.0\.0\.1:(\d+)", _read("deploy", "env.server.example"), re.M))
    assert registered <= set(_shops()), registered - set(_shops())
    assert configured <= set(_shops()), configured - set(_shops())
