#!/usr/bin/env python3
"""F1 regression: real Git, synthetic files only; no build, SSH or decryption."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vps", SCRIPTS / "vps.py")
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)
spec = importlib.util.spec_from_file_location("ops", SCRIPTS / "vps-vless-ops.py")
O = importlib.util.module_from_spec(spec)
spec.loader.exec_module(O)


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "checkout with spaces"
        self.repo.mkdir()
        self.git("init", "-q")
        self.write(".gitignore", "ignored-canary\n.local/\nhosts/generated/*/credentials.json\n")
        self.write("flake.lock", '{"nodes":{"root":{"inputs":{}}},"root":"root","version":7}')
        self.write("flake.nix", '''{
          outputs = { self }: let
            native = import ./hosts/inventory.nix;
            generated = builtins.fromJSON (builtins.readFile ./hosts/generated/inventory.json);
          in { nixosConfigurations = builtins.mapAttrs (name: host: { config = {
            inherit (host) origin;
            declaration = builtins.readFile (./. + "/hosts/generated/${name}/configuration.nix");
            ciphertext = builtins.readFile (./. + "/hosts/generated/${name}/secrets.yaml");
            sourcePath = toString self;
            privateFilesPresent = builtins.any (path: builtins.pathExists (self + path)) [
              "/ignored-canary" "/hosts/generated/fixture/credentials.json"
              "/.local/state/vps/fixture/credentials.json"
            ];
          }; }) (native // generated); };
        }''')
        self.write("hosts/inventory.nix", '{ fixture = { origin = "native"; }; }')
        self.git("add", ".")  # Fixture index only; no commit required.
        self.write("hosts/generated/inventory.json", '{"fixture":{"origin":"generated"}}')
        for name in (*V.HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"):
            self.write("hosts/generated/fixture/" + name, "synthetic-" + name)
        self.forbidden = ["ignored-canary", ".local/state/vps/fixture/credentials.json",
                          "hosts/generated/fixture/credentials.json", "hosts/generated/fixture/errors/report.json",
                          "hosts/generated/fixture/bootstrap/etc/ssh/ssh_host_ed25519_key"]
        for name in self.forbidden:
            self.write(name, "SYNTHETIC-PRIVATE-CANARY")
            (self.repo / name).chmod(0o600)
        self.write("../external-state/vps/fixture/credentials.json", "SYNTHETIC-XDG-STATE")
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.repo / ".local/state")})
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True).stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def snapshot_path(self, reference):
        self.assertTrue(reference.startswith("path:"))
        path = Path(reference[5:].split("#", 1)[0])
        self.assertNotEqual(path, self.repo)
        self.assertTrue(path.is_dir())
        return path

    def test_allowlist_excludes_both_state_layouts_without_opening_them(self):
        # Even accidental force-adds cannot admit legacy or XDG state.
        self.git("add", "-f", *self.forbidden[1:])
        index = self.git("ls-files", "--stage", "-z")
        copied = []
        real_copy = V.copy_source_file
        def copy(repo, relative, target):
            self.assertNotIn(relative.as_posix(), self.forbidden)
            copied.append(relative.as_posix())
            real_copy(repo, relative, target)
        with patch.object(V, "copy_source_file", side_effect=copy), V.ref(self.repo, "fixture") as reference:
            target = self.snapshot_path(reference)
            expected = {".gitignore", "flake.nix", "flake.lock", "hosts/inventory.nix",
                        "hosts/generated/inventory.json"}
            expected.update("hosts/generated/fixture/" + name for name in
                            (*V.HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"))
            self.assertEqual(set(copied), expected)
            self.assertEqual({p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}, expected)
            for name in expected:
                self.assertEqual((target / name).read_bytes(), (self.repo / name).read_bytes())
            self.assertFalse((target / ".git").exists())
        self.assertFalse(target.exists())
        self.assertEqual(self.git("ls-files", "--stage", "-z"), index)
        for name in self.forbidden:
            self.assertEqual((self.repo / name).read_text(), "SYNTHETIC-PRIVATE-CANARY")

    def test_equivalent_xdg_paths_exclude_force_added_state_before_copy(self):
        state = self.forbidden[1]
        self.git("add", "-f", state)
        # A sibling sharing the VPS prefix is source, not part of the state root.
        sibling = ".local/state/vps-source.nix"
        self.write(sibling, "synthetic-source")
        self.git("add", "-f", sibling)
        index = self.git("ls-files", "--stage", "-z")
        canonical = str(self.repo / ".local/state")
        paths = (canonical, canonical + "/.",
                 str(self.repo / ".local/../.local/state"),
                 str(self.repo / "missing/../.local/state"),
                 str(self.repo.parent / "absent/../" / self.repo.name / ".local/state"),
                 str(self.repo) + "//.local//state//")
        real_copy = V.copy_source_file
        def copy(repo, relative, target):
            # Assert before the copier can open the synthetic credential.
            self.assertNotEqual(relative.as_posix(), state)
            real_copy(repo, relative, target)
        for xdg in paths:
            with self.subTest(xdg=xdg), patch.dict(os.environ, {"XDG_STATE_HOME": xdg}), \
                    patch.object(V, "copy_source_file", side_effect=copy):
                with V.ref(self.repo, "fixture") as reference:
                    target = self.snapshot_path(reference)
                    self.assertFalse((target / state).exists())
                    self.assertEqual((target / sibling).read_text(), "synthetic-source")
                    for name in (*V.HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"):
                        self.assertEqual((target / "hosts/generated/fixture" / name).read_text(),
                                         "synthetic-" + name)
                self.assertFalse(target.exists())
        self.assertEqual(self.git("ls-files", "--stage", "-z"), index)

    def test_custom_in_checkout_override_is_refused_before_any_consumer(self):
        root = self.repo / "private-workflow"
        ignore = self.repo / ".gitignore"
        ignore.write_text(ignore.read_text() + "private-workflow/\n")
        credential = "private-workflow/fixture/credentials.json"
        self.write(credential, "SYNTHETIC-OVERRIDE-CREDENTIAL")
        self.write("private-workflow/fixture/errors/report.json", "SYNTHETIC-OVERRIDE-DIAGNOSTIC")
        sibling = "private-workflow-source.nix"
        self.write(sibling, "synthetic-neighbour-source")
        self.git("add", "-f", credential, "private-workflow/fixture/errors/report.json", sibling)
        index = self.git("ls-files", "--stage", "-z")
        roots = (root, root / "../private-workflow", self.repo / "missing/../private-workflow",
                 self.repo.parent / "absent/.." / self.repo.name / "private-workflow",
                 self.repo / "hosts/../private-workflow")
        real_open, real_load = Path.open, V.load
        def guarded_open(path, *args, **kwargs):
            self.assertFalse(Path(os.path.abspath(path)).is_relative_to(root),
                             "override state opened before refusal")
            return real_open(path, *args, **kwargs)
        # Test the common selector as well as both workflow/operations entry
        # paths. All later consumers (eval, build, VM test, installer fallback)
        # must be unreachable, not merely taught to ignore the credential later.
        for override in roots:
            with self.subTest(override=str(override)), \
                    patch.object(Path, "open", autospec=True, side_effect=guarded_open), \
                    patch.object(V, "copy_source_file") as copier, patch.object(V, "run") as runner:
                with self.assertRaisesRegex(V.Error, "In-checkout --state-dir"):
                    V.state_path(self.repo, "fixture", override, create=True)
                for command in (("check", "fixture", "--vm-test"), ("apply", "fixture", "--check"),
                                ("install", "fixture"), ("verify", "fixture")):
                    with self.subTest(command=command[0]), \
                            patch.object(V.sys, "argv", ["vps-vless", "--repo", str(self.repo),
                                                        "--state-dir", str(override), *command]), \
                            patch.object(V.os, "umask"), patch.object(V.signal, "signal"), \
                            patch.object(V, "check") as check, patch.object(V, "install") as install, \
                            patch.object(V, "verify") as verify, patch.object(O, "dispatch") as dispatch, \
                            patch.object(V, "load", side_effect=lambda path, name:
                                         O if Path(path).name == "vps-vless-ops.py" else real_load(path, name)), \
                            patch.object(V, "report_failure") as failure:
                        self.assertEqual(V.main(), 1)
                        self.assertIsInstance(failure.call_args.args[0], V.Error)
                        self.assertIn("In-checkout --state-dir", str(failure.call_args.args[0]))
                        self.assertIsNone(failure.call_args.args[2])
                        for consumer in (check, install, verify, dispatch):
                            consumer.assert_not_called()
                copier.assert_not_called()
                runner.assert_not_called()
        self.assertEqual(self.git("ls-files", "--stage", "-z"), index)
        self.assertEqual((self.repo / credential).read_text(), "SYNTHETIC-OVERRIDE-CREDENTIAL")
        self.assertEqual((self.repo / sibling).read_text(), "synthetic-neighbour-source")

    def test_external_and_known_layout_overrides_remain_supported(self):
        external = self.repo.parent / "external-state/vps"
        legacy = self.repo / "hosts/generated"
        xdg = self.repo / ".local/state/vps"
        for root in (external, legacy, xdg, external / "absent/..", xdg / "missing/.."):
            with self.subTest(root=str(root)):
                self.assertEqual(V.state_path(self.repo, "fixture", root),
                                 Path(os.path.abspath(root)) / "fixture")
        # Default lookup still selects existing legacy artifacts, then XDG when
        # those artifacts are absent. No real state or identities are involved.
        self.write("hosts/generated/fixture/plan.json", "{}")
        with self.assertRaisesRegex(V.Error, "Unsafe/ambiguous VPS state"):
            V.state_path(self.repo, "fixture")
        (xdg / "fixture/credentials.json").unlink()
        (xdg / "fixture").rmdir()
        self.assertEqual(V.state_path(self.repo, "fixture"), legacy / "fixture")
        (legacy / "fixture/plan.json").unlink()
        (legacy / "fixture/credentials.json").unlink()
        (legacy / "fixture/errors/report.json").unlink()
        self.write(".local/state/vps/fixture/credentials.json", "SYNTHETIC-PRIVATE-CANARY")
        self.write(".local/state/vps/fixture/.state-owner.json", json.dumps({"repo": str(self.repo)}))
        self.assertEqual(V.state_path(self.repo, "fixture"), xdg / "fixture")
        sibling = "private-workflow-source.nix"
        self.write(sibling, "synthetic-neighbour-source")
        self.git("add", "-f", sibling, self.forbidden[1])
        real_copy = V.copy_source_file
        def copy(repo, relative, target):
            self.assertNotIn(relative.as_posix(), self.forbidden)
            real_copy(repo, relative, target)
        with patch.object(V, "copy_source_file", side_effect=copy), V.ref(self.repo, "fixture") as reference:
            target = self.snapshot_path(reference)
            self.assertEqual((target / sibling).read_text(), "synthetic-neighbour-source")
            for name in (*V.HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"):
                self.assertEqual((target / "hosts/generated/fixture" / name).read_text(), "synthetic-" + name)
        self.assertFalse(target.exists())

    def test_working_tree_edits_deletions_and_untracked_source(self):
        self.write("tracked.nix", "before")
        self.git("add", "tracked.nix")
        self.write("tracked.nix", "after")
        self.write("untracked.nix", "not selected")
        (self.repo / "hosts/inventory.nix").unlink()
        with V.ref(self.repo, "fixture") as reference:
            target = self.snapshot_path(reference)
            self.assertEqual((target / "tracked.nix").read_text(), "after")
            self.assertFalse((target / "untracked.nix").exists())
            self.assertFalse((target / "hosts/inventory.nix").exists())

    def test_symlinked_tracked_file_parent_and_generated_ciphertext_fail_closed(self):
        outside = self.repo.parent / "private"
        outside.mkdir()
        (outside / "secret").write_text("SYNTHETIC-PRIVATE")
        (outside / "file.nix").write_text("SYNTHETIC-PRIVATE")
        cases = ("tracked.nix", "nested/file.nix", "hosts/generated/fixture/secrets.yaml",
                 "hosts/generated/inventory.json", "hosts/generated/fixture")
        for relative in cases:
            with self.subTest(relative=relative):
                path = self.repo / relative
                if relative == "hosts/generated/fixture":
                    saved = path.with_name("fixture-saved")
                    path.rename(saved)
                    path.symlink_to(outside, target_is_directory=True)
                else:
                    original = path.read_bytes() if path.exists() else None
                    self.write(relative, "source")
                    if relative in ("tracked.nix", "nested/file.nix"):
                        self.git("add", relative)
                    if relative == "nested/file.nix":
                        path.unlink()
                        path.parent.rmdir()
                        path.parent.symlink_to(outside, target_is_directory=True)
                    else:
                        path.unlink()
                        path.symlink_to(outside / "secret")
                with self.assertRaises(V.Error), V.ref(self.repo, "fixture"):
                    self.fail("unsafe source yielded a Nix reference")
                if relative == "hosts/generated/fixture":
                    path.unlink()
                    saved.rename(path)
                elif relative == "nested/file.nix":
                    path.parent.unlink()
                    self.git("rm", "--cached", relative)
                else:
                    path.unlink()
                    if original is not None:
                        path.write_bytes(original)
                    if relative == "tracked.nix":
                        self.git("rm", "--cached", relative)

    def test_indexed_symlink_submodule_and_special_file_are_refused(self):
        link = self.repo / "link.nix"
        link.symlink_to("flake.nix")
        self.git("add", "link.nix")
        with self.assertRaisesRegex(V.Error, "Symlink or submodule"), V.ref(self.repo, "fixture"):
            self.fail("indexed symlink accepted")
        link.unlink()
        self.git("rm", "--cached", "link.nix")
        self.git("update-index", "--add", "--cacheinfo", "160000," + "a" * 40 + ",submodule")
        with self.assertRaisesRegex(V.Error, "Symlink or submodule"), V.ref(self.repo, "fixture"):
            self.fail("submodule accepted")
        self.git("rm", "--cached", "submodule")
        self.write("special.nix", "regular before replacement")
        self.git("add", "special.nix")
        (self.repo / "special.nix").unlink()
        os.mkfifo(self.repo / "special.nix")
        with self.assertRaisesRegex(V.Error, "Non-regular"), V.ref(self.repo, "fixture"):
            self.fail("FIFO accepted")

    def test_conflicted_generated_index_is_refused(self):
        oid = self.git("rev-parse", ":flake.nix").strip()
        entry = b"100644 " + oid + b" 1\thosts/generated/fixture/configuration.nix\n"
        subprocess.run(["git", "-C", str(self.repo), "update-index", "--index-info"],
                       input=entry, check=True, capture_output=True)
        with self.assertRaisesRegex(V.Error, "Conflicted"), V.ref(self.repo, "fixture"):
            self.fail("conflicted generated declaration accepted")

    def test_missing_inventory_with_only_state_is_not_silently_erased(self):
        for name in (*V.HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"):
            (self.repo / "hosts/generated/fixture" / name).unlink()
        (self.repo / "hosts/generated/inventory.json").unlink()
        with self.assertRaisesRegex(V.Error, "restore the registry"), V.ref(self.repo, "fixture"):
            self.fail("missing registry accepted")

    def test_non_git_export_and_nonroot_checkout_never_fall_back(self):
        export = self.repo.parent / "export"
        export.mkdir()
        for directory in (export, self.repo / "hosts"):
            with self.subTest(directory=directory), self.assertRaises(V.Error), V.ref(directory, "fixture"):
                self.fail("unsafe root accepted")

    def test_evaluate_uses_live_snapshot_and_removes_it_on_nix_failure(self):
        real_run, snapshots = V.run, []
        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            self.assertEqual(command[:2], ["nix", "eval"])
            snapshots.append(self.snapshot_path(command[-1]))
            self.assertEqual(command[-1].split("#")[1], "nixosConfigurations.fixture.config.origin")
            raise V.Error("synthetic Nix failure")
        with patch.object(V, "run", side_effect=run), self.assertRaisesRegex(V.Error, "synthetic Nix failure"):
            V.evaluate(self.repo, "fixture", "origin")
        self.assertEqual(len(snapshots), 1)
        self.assertFalse(snapshots[0].exists())

    def test_apply_and_installer_fallback_build_only_the_snapshot(self):
        real_run, snapshots = V.run, []
        system = "/nix/store/" + "a" * 32 + "-synthetic-system"
        tools = "/nix/store/" + "b" * 32 + "-synthetic-tools"
        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            if command[:2] == ["nix", "copy"]:
                return b""
            self.assertEqual(command[:2], ["nix", "build"])
            snapshots.append(self.snapshot_path(command[2]))
            return (tools if command[2].endswith("#vps-installer-tools") else system).encode()
        real_is_dir = Path.is_dir
        with patch.object(V, "run", side_effect=run), patch.object(O, "fingerprint", return_value="same"), \
                patch.object(O, "validate_runtime"), patch.object(O, "administrative_connection") as connection:
            host = argparse.Namespace(repo=self.repo, name="fixture")
            self.assertEqual(O.apply(V, argparse.Namespace(check=True), host), 0)
            connection.assert_not_called()
            with patch.dict(os.environ, {}, clear=True), patch.object(V, "remote_run", return_value=b"installer\n"), \
                    patch.object(V.Path, "is_dir", autospec=True,
                                 side_effect=lambda path: str(path) == tools or real_is_dir(path)):
                c = V.connection("203.0.113.5", "root", 22, "/synthetic-key", "/synthetic-known")
                self.assertEqual(V.prepare_installer_tools(c, self.repo)["installer_tools"], tools)
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(all(not path.exists() for path in snapshots))

    @unittest.skipUnless(shutil.which("nix"), "optional offline eval requires Nix; pure staging tests still run")
    def test_offline_nix_eval_preserves_generated_precedence_and_ciphertext(self):
        # Only this synthetic, input-free flake reaches Nix, never the real repo.
        with V.source_ref(self.repo, "nixosConfigurations.fixture.config") as reference:
            target = self.snapshot_path(reference)
            result = V.run(["nix", "eval", "--json", "--offline", "--no-write-lock-file",
                            "--option", "allow-import-from-derivation", "false", reference], timeout=60)
            value = json.loads(result)
            self.assertTrue(value.pop("sourcePath").startswith("/nix/store/"))
            self.assertEqual(value, {"origin": "generated", "privateFilesPresent": False,
                             "declaration": "synthetic-configuration.nix", "ciphertext": "synthetic-secrets.yaml"})
            self.assertFalse((target / "ignored-canary").exists())
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
