"""Deck: the templated netlists for every testbench × corner, stored under ``.icopt/decks/<fp>/``.

The deck's identity (:meth:`Deck.fingerprint`, N-99) is everything it hands the simulation: the template texts and every
file of every testbench's bundle -- the exported netlist directory with Maestro's support files (``.modelFiles``,
``.designVariables``, ``amap/``, ...) -- by relative path and content, in a fixed order. A symlink in a bundle counts by
the content it points to, as ``Deck.save`` copies it (``copytree(symlinks=False)``); file modes and times do not count.
Two decks with the same templates and different support files are two decks: ``Deck.save`` writes each under its own
``decks/<fp>/`` and never replaces another deck's bundle, and a deck loaded from ``decks/<fp>/`` fingerprints to
``<fp>``. Files outside the bundle that a netlist names -- a PDK's model files by absolute path, Verilog-A, an sNp --
are not part of it: the deck's identity is the exported netlist and its support files, as Maestro wrote them.

The render stages carry the fingerprint as their ``identity`` (``stages.spectre_chain.Render``,
``stages.em_chain.BindNport``), so it is part of the pipeline fingerprint: an observation is never reused for a deck
other than the one it was simulated with (``eval.engine``, "Identity").
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ic_opt.localpath import literal

Key = tuple[str, str | None]  # (testbench id, corner id or None)


@dataclass
class Deck:
    templates: dict[Key, str] = field(default_factory=dict)   # template text per testbench × corner
    source: dict[str, str] = field(default_factory=dict)      # testbench id -> where its deck came from
    bundles: dict[str, Path] = field(default_factory=dict)    # testbench id -> exported netlist dir (support files)
    # testbench id -> {relative path: sha256} of a bundle whose files are no longer there: the preview's deck (``--plan``,
    # ``netlist.import``), read before its temporary fetch was removed (:meth:`snapshot`). Such a deck cannot run.
    digests: dict[str, dict[str, str]] = field(default_factory=dict)

    def template(self, testbench: str, corner: str | None) -> str:
        return self.templates[(testbench, corner)]

    def bundle(self, testbench: str) -> Path | None:
        return self.bundles.get(testbench)

    def support_files(self, testbench: str) -> dict[str, str]:
        """``{relative path: sha256}`` of the testbench's bundle, read from its files (``digests`` for a snapshot)."""
        bundle = self.bundles.get(testbench)
        return tree_digests(bundle) if bundle is not None else dict(self.digests.get(testbench, {}))

    def fingerprint(self) -> str:
        """The templates, then every bundle's files by relative path and content hash (module docstring). A deck without
        bundles fingerprints as it did before N-99. Read afresh on every call: a bundle's files are its content."""
        h = hashlib.sha256()
        for (tb, corner), text in sorted(self.templates.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
            h.update(f"{tb}/{corner}\n".encode())
            h.update(text.encode())
        for tb in sorted(set(self.bundles) | set(self.digests)):
            h.update(f"\0bundle {tb}\n".encode())
            for rel, sha in self.support_files(tb).items():
                h.update(json.dumps([rel, sha]).encode() + b"\n")
        return h.hexdigest()[:16]

    def describe(self) -> str:
        """``deck <fp> (<n> testbenches, <m> support files)``: the line the plan and ``ic-opt doctor`` print (N-99)."""
        testbenches = {tb for tb, _ in self.templates} | set(self.bundles) | set(self.digests)
        files = sum(len(self.support_files(tb)) for tb in set(self.bundles) | set(self.digests))
        return f"deck {self.fingerprint()} ({len(testbenches)} testbenches, {files} support files)"

    def snapshot(self) -> Deck:
        """The same deck with each bundle's files replaced by their digests: it fingerprints the same after the files are
        gone (the preview's temporary fetch), and cannot run or be saved."""
        return Deck(templates=dict(self.templates), source=dict(self.source),
                    digests={**self.digests, **{tb: tree_digests(b) for tb, b in self.bundles.items()}})

    def save(self, root: Path) -> Path:
        if self.digests:
            raise ValueError("a deck snapshot (the preview's) holds its support files' digests, not the files: it cannot be saved")
        target = root / self.fingerprint()
        target.mkdir(parents=True, exist_ok=True)       # a deck without templates (a devices-only spec) still saves its source.txt
        for (tb, corner), text in self.templates.items():
            path = target / tb / (corner or "nominal") / "template.scs"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for tb, bundle in self.bundles.items():
            dst = target / tb / "bundle"
            if bundle.resolve() != dst.resolve():        # literal: an export's names may end in a dot (localpath)
                # the same fingerprint, the same files: a tree left by a save that stopped part-way is written again whole
                shutil.rmtree(literal(dst), ignore_errors=True)
                shutil.copytree(literal(bundle), literal(dst), symlinks=False)
        (target / "source.txt").write_text("".join(f"{tb}\t{src}\n" for tb, src in sorted(self.source.items())), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path) -> Deck:
        deck = cls()
        for bundle in sorted(path.glob("*/bundle")):
            deck.bundles[bundle.parent.name] = bundle
        for template in sorted(path.glob("*/*/template.scs")):
            corner = template.parent.name
            deck.templates[(template.parent.parent.name, None if corner == "nominal" else corner)] = template.read_text(
                encoding="utf-8"
            )
        source = path / "source.txt"
        if source.exists():
            for line in source.read_text(encoding="utf-8").splitlines():
                tb, _, src = line.partition("\t")
                deck.source[tb] = src
        return deck


def tree_digests(root: Path) -> dict[str, str]:
    """``{relative path: sha256}`` of every file under ``root``, sorted by path (``/`` between names on every platform).
    Symlinks are followed, files and directories alike, as ``copytree(symlinks=False)`` copies them; a dangling one is an
    error there and here. Empty directories, file modes and times are not part of it. Through ``literal``: an export's
    names may end in a dot (``localpath``)."""
    base = literal(root)
    files: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(base, followlinks=True, onerror=_raise):
        rel = os.path.relpath(dirpath, base)
        prefix = "" if rel == os.curdir else rel.replace(os.sep, "/") + "/"
        for name in filenames:
            h = hashlib.sha256()
            with open(os.path.join(dirpath, name), "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    h.update(chunk)
            files[prefix + name] = h.hexdigest()
    return dict(sorted(files.items()))


def _raise(error: OSError) -> None:
    raise error                 # a directory that cannot be read is an error, not a bundle without it
