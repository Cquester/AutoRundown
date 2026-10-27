"""Fetch pieces of the CHARM torrent: from the swarm (libtorrent), or, for
tests and dev.py, from a complete local copy.

The rules the user agreed for LEMON downloads (2026-10-09) hold here too:
only the swarm, never the website over HTTP; never seed -- upload is capped
while running and the session is closed as soon as the pieces are in; no
router ports opened (no UPnP / NAT-PMP); the data goes in its own folder.
"""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from .charm import Layout

UPLOAD_CAP = 4 * 1024          # bytes/s while running; libtorrent needs a trickle to trade
EXTRA_PEERS = [("charm.li", 17471)]   # the site's own "add charm.li:17471 as a peer"


class Swarm:
    """What the downloader needs: the layout, which pieces are on disk, and
    a way to get more."""
    layout: Layout
    root: Path

    def have(self) -> set[int]:
        raise NotImplementedError

    def zero_pieces(self) -> set[int]:
        """Pieces that are all zeros: on disk without being fetched."""
        return set()

    def fetch(self, pieces, progress=None, cancelled=None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class Cancelled(Exception):
    pass


def layout_of_torrent(torrent: Path) -> Layout:
    import libtorrent as lt
    ti = lt.torrent_info(str(torrent))
    fs = ti.files()
    return Layout(ti.piece_length(), [(fs.file_path(i), fs.file_size(i))
                                      for i in range(fs.num_files())])


class TorrentSwarm(Swarm):
    """The real swarm. Resume data is kept in the work folder, so a plan's
    pieces are not fetched again by the download that follows it."""

    def __init__(self, torrent: Path, work: Path, log=print):
        import libtorrent as lt
        self.lt, self.log = lt, log
        self.ti = lt.torrent_info(str(torrent))
        fs = self.ti.files()
        self.layout = Layout(self.ti.piece_length(),
                             [(fs.file_path(i), fs.file_size(i)) for i in range(fs.num_files())])
        self.root = Path(work) / "data"     # files land at data/operation-charm/...
        self.root.mkdir(parents=True, exist_ok=True)
        self.resume = Path(work) / "resume.dat"
        self.session = lt.session({
            "listen_interfaces": "0.0.0.0:0,[::]:0",     # any free port, closed with the session
            "enable_upnp": False, "enable_natpmp": False, "enable_lsd": False,
            "enable_dht": True,
            "upload_rate_limit": UPLOAD_CAP,
            "alert_mask": lt.alert_category.error | lt.alert_category.storage
                          | lt.alert_category.status,
        })
        if self.resume.is_file():
            p = lt.read_resume_data(self.resume.read_bytes())
        else:
            p = lt.add_torrent_params()
        p.ti = self.ti
        p.save_path = str(self.root)
        # added wanting nothing (priorities in the add itself: a plain add
        # wants every piece until told otherwise) and not paused -- a paused
        # torrent never checks its files, so waiting for the check hung; a
        # peer added while the files are being checked is dropped (a real
        # run sat at 0 peers), so peers come after the check
        p.flags &= ~(lt.torrent_flags.auto_managed | lt.torrent_flags.seed_mode
                     | lt.torrent_flags.paused)
        p.piece_priorities = [0] * self.ti.num_pieces()
        self.h = self.session.add_torrent(p)
        self._wait_checked()
        self._add_peers()

    def _add_peers(self) -> None:
        for host, port in EXTRA_PEERS:
            try:
                for *_, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                    self.h.connect_peer((addr[0], port))
            except OSError:
                pass

    def _wait_checked(self) -> None:
        lt = self.lt
        last = time.time()
        while (st := self.h.status()).state in (lt.torrent_status.checking_files,
                                                lt.torrent_status.checking_resume_data):
            if time.time() - last >= 30:
                self.log(f"checking the pieces on disk: {st.progress * 100:.0f}%")
                last = time.time()
            time.sleep(0.5)

    def have(self) -> set[int]:
        return {i for i in range(self.ti.num_pieces()) if self.h.have_piece(i)}

    def zero_pieces(self) -> set[int]:
        import hashlib
        z = hashlib.sha1(bytes(self.ti.piece_length())).digest()
        return {i for i in range(self.ti.num_pieces()) if self.ti.hash_for_piece(i) == z}

    def fetch(self, pieces, progress=None, cancelled=None) -> None:
        want = sorted({p for p in pieces if not self.h.have_piece(p)})
        if not want:
            return
        for p in want:
            self.h.piece_priority(p, 7)
        last, lonely = 0.0, time.time()
        self.h.force_reannounce()
        self.h.force_dht_announce()
        try:
            while True:
                done = sum(1 for p in want if self.h.have_piece(p))
                if done == len(want):
                    break
                if cancelled and cancelled():
                    raise Cancelled()
                for a in self.session.pop_alerts():
                    if isinstance(a, (self.lt.file_error_alert, self.lt.torrent_error_alert)):
                        raise OSError(a.message())
                if self.h.status().num_peers:
                    lonely = time.time()
                elif time.time() - lonely > 90:
                    # nobody yet: ask the trackers, the DHT and CHARM's own peer again
                    self.log("no peers yet; asking the trackers and charm.li:17471 again")
                    self.h.force_reannounce()
                    self.h.force_dht_announce()
                    self._add_peers()
                    lonely = time.time()
                if progress and time.time() - last >= 5:
                    st = self.h.status()
                    progress(done, len(want), st.download_payload_rate, st.num_peers,
                             st.total_payload_upload)
                    last = time.time()
                time.sleep(0.5)
        finally:
            for p in want:
                if not self.h.have_piece(p):
                    self.h.piece_priority(p, 0)
        self.h.flush_cache()
        if progress:
            st = self.h.status()
            progress(len(want), len(want), 0, st.num_peers, st.total_payload_upload)

    def close(self) -> None:
        """Stop sharing at once, keep the resume data, close the session."""
        if self.session is None:
            return
        lt = self.lt
        self.h.pause()
        self.h.save_resume_data(lt.torrent_handle.save_info_dict)
        end = time.time() + 30
        while time.time() < end:
            for a in self.session.pop_alerts():
                if isinstance(a, lt.save_resume_data_alert):
                    self.resume.write_bytes(lt.write_resume_data_buf(a.params))
                    end = 0
                elif isinstance(a, lt.save_resume_data_failed_alert):
                    end = 0
            time.sleep(0.2)
        self.session.remove_torrent(self.h)
        self.session = None


class LocalSwarm(Swarm):
    """A complete copy on disk standing in for the swarm (tests, dev.py):
    copies each asked-for piece into the work folder and counts it, so a
    test can check exactly what a download would have fetched."""

    def __init__(self, source: Path, work: Path, log=print):
        self.source = Path(source)
        meta = json.loads((self.source / "layout.json").read_text(encoding="utf-8"))
        self.layout = Layout(meta["piece_length"], [tuple(f) for f in meta["files"]])
        self.root = Path(work) / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = Path(work) / "local_have.json"
        self._have = set(json.loads(self.state.read_text())) if self.state.is_file() else set()
        self.fetched: list[int] = []

    def have(self) -> set[int]:
        return set(self._have)

    def fetch(self, pieces, progress=None, cancelled=None) -> None:
        want = sorted({p for p in pieces if p not in self._have})
        pl = self.layout.piece_length
        for n, p in enumerate(want):
            if cancelled and cancelled():
                raise Cancelled()
            a, b = p * pl, min((p + 1) * pl, self.layout.total)
            for path, (start, size) in self.layout.files.items():
                lo, hi = max(a, start), min(b, start + size)
                if lo >= hi:
                    continue
                with open(self.source / path, "rb") as src:
                    src.seek(lo - start)
                    data = src.read(hi - lo)
                dst = self.root / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "r+b" if dst.exists() else "wb") as f:
                    f.seek(lo - start)
                    f.write(data)
            self._have.add(p)
            self.fetched.append(p)
            if progress:
                progress(n + 1, len(want), 0, 0, 0)
        self.state.write_text(json.dumps(sorted(self._have)))
