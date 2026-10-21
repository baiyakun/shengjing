import io
import tarfile

from shengjing_engine.core.extensions import installed, sha256_file

from .test_workflows import run_task


def make_pack(path, name="analysis", *, evil=False):
    with tarfile.open(path, "w:gz") as tar:
        data = b"#!/bin/sh\necho hi\n"
        info = tarfile.TarInfo(f"{name}/bin/python3")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
        if evil:
            bad = tarfile.TarInfo("../../escape.txt")
            bad.size = 1
            tar.addfile(bad, io.BytesIO(b"x"))
    return path


async def test_install_from_local_archive_and_detect_worker(services, tmp_path):
    archive = make_pack(tmp_path / "analysis.tar.gz")
    assert not services.workers.available("analysis")
    task = await run_task(services, "extension.install", {
        "name": "analysis", "archive_path": str(archive), "sha256": sha256_file(archive)})
    assert task["status"] == "succeeded", task["error"]
    assert installed(services.paths.extensions)["analysis"]
    python = services.paths.extensions / "analysis" / "bin" / "python3"
    assert python.stat().st_mode & 0o100  # 可执行位保留
    assert services.workers.available("analysis")


async def test_rejects_bad_checksum_and_path_traversal(services, tmp_path):
    archive = make_pack(tmp_path / "a.tar.gz")
    bad = await run_task(services, "extension.install", {
        "name": "analysis", "archive_path": str(archive), "sha256": "0" * 64})
    assert bad["status"] == "failed" and "校验值" in bad["error"]
    evil = make_pack(tmp_path / "evil.tar.gz", evil=True)
    task = await run_task(services, "extension.install", {
        "name": "analysis", "archive_path": str(evil)})
    assert task["status"] == "failed" and "解压失败" in task["error"]
    assert not (tmp_path / "escape.txt").exists()


async def test_url_install_requires_checksum(services):
    task = await run_task(services, "extension.install", {
        "name": "music", "url": "https://example.com/music.tar.gz"})
    assert task["status"] == "failed" and "sha256" in task["error"]
