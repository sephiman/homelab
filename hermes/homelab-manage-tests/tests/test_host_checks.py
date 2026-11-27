import subprocess
from pathlib import Path

from fake_host import FakeHost


def _certificate(path: Path, days: int, *domains: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    san = ",".join(f"DNS:{domain}" for domain in domains)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
         "-keyout", str(path.with_name("privkey.pem")), "-out", str(path), "-days", str(days),
         "-subj", f"/CN={domains[0]}", "-addext", f"subjectAltName={san}"],
        check=True,
        capture_output=True,
    )  # fmt: skip


def test_cert_status_lists_certificates_fewest_days_first_and_flags_failing_renewals(
    host: FakeHost, tmp_path: Path
) -> None:
    letsencrypt, data = tmp_path / "letsencrypt", tmp_path / "data"
    _certificate(letsencrypt / "archive/npm-1/fullchain1.pem", 80, "ha.example.org")
    (letsencrypt / "live/npm-1").mkdir(parents=True)
    (letsencrypt / "live/npm-1/fullchain.pem").symlink_to("../../archive/npm-1/fullchain1.pem")
    _certificate(data / "custom_ssl/npm-2/fullchain.pem", 5, "grafana.example.org", "logs.example.org")
    host.mount("nginx-proxy-manager", "/etc/letsencrypt", letsencrypt)
    host.mount("nginx-proxy-manager", "/data", data)

    result = host.run("cert-status")

    assert result.returncode == 0, result.stderr
    soon, later = result.stdout.splitlines()
    assert "grafana.example.org,logs.example.org" in soon
    assert "RENEW SOON" in soon
    assert "ha.example.org" in later
    assert "RENEW SOON" not in later


def test_cert_status_explains_when_the_proxy_is_missing(host: FakeHost) -> None:
    result = host.run("cert-status")

    assert result.returncode == 2
    assert "no nginx-proxy-manager container" in result.stderr


def test_disk_health_summarises_each_disk_even_when_smartctl_warns(host: FakeHost) -> None:
    host.disks(
        [("/dev/sda", "sat"), ("/dev/nvme0", "nvme"), ("/dev/sdb", "sat")],
        {
            "/dev/sda": {
                "model_name": "WDC WD40EFRX",
                "smart_status": {"passed": True},
                "temperature": {"current": 34},
                "power_on_time": {"hours": 21000},
                "ata_smart_attributes": {"table": [
                    {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
                    {"id": 9, "name": "Power_On_Hours", "raw": {"value": 21000}},
                    {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 8}},
                ]},
            },
            "/dev/nvme0": {
                "model_name": "Samsung 990 PRO",
                "smart_status": {"passed": False},
                "nvme_smart_health_information_log": {
                    "percentage_used": 3, "media_errors": 12, "critical_warning": 4,
                },
            },
        },
    )  # fmt: skip

    result = host.run("disk-health")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "/dev/sda  WDC WD40EFRX  PASSED",
        "  temperature 34°C",
        "  powered on 21000 h",
        "  Reallocated_Sector_Ct: 0",
        "  Current_Pending_Sector: 8",
        "/dev/nvme0  Samsung 990 PRO  FAILED",
        "  wear 3% used, 12 media errors, critical warning 4",
        "/dev/sdb: could not read SMART data",
    ]


def test_disk_health_says_so_when_no_disk_is_found(host: FakeHost) -> None:
    host.disks([], {})

    assert host.run("disk-health").stdout == "no disks found by smartctl --scan\n"
