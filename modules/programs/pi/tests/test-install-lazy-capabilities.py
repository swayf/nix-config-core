"""All-profile delivery, preservation, and real Pi registration without model calls."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "install-lazy-capabilities.sh"
SOURCE = HERE / "extensions/lazy-capabilities"
PI = os.environ.get("PI_BIN") or shutil.which("pi")


class InstallLazyCapabilitiesTest(unittest.TestCase):
    def install(self, home, source=SOURCE, check=True):
        return subprocess.run(["bash", str(SCRIPT), str(source)],
                              env={**os.environ, "HOME": str(home)}, check=check,
                              capture_output=True, text=True)

    def test_profiles_repeated_install_settings_and_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            custom = home / ".pi/profiles/custom"
            custom.mkdir(parents=True)
            settings = custom / "settings.json"
            original = '{"defaultModel":"custom","defaultTools":["read"],"skills":["extra"]}\n'
            settings.write_text(original)
            override = home / ".pi/profiles/override/extensions/lazy-capabilities"
            override.mkdir(parents=True)
            (override / "index.ts").write_text("local extension\n")
            external = root / "external"
            external.mkdir()
            (home / ".pi/profiles/alias").symlink_to(external)
            extensions_alias = home / ".pi/profiles/extension-alias"
            extensions_alias.mkdir()
            (extensions_alias / "extensions").symlink_to(external)
            for version in (1, 1, 2):
                source = root / f"source v{version}"
                # Повторная установка использует ту же immutable-копию из store.
                if not source.exists():
                    shutil.copytree(SOURCE, source)
                self.install(home, source)
                for profile in (home / ".pi/agent", custom, *[home / ".pi/profiles" / p for p in ("general", "code", "wiki")]):
                    target = profile / "extensions/lazy-capabilities"
                    self.assertEqual(target.resolve(), source)
                    self.assertEqual((target / "index.ts").read_bytes(), (SOURCE / "index.ts").read_bytes())
                    self.assertTrue((target / "core.mjs").is_file())
                expected = json.loads(original)
                expected["skills"] += ["-~/.agents/skills/terminal-browser", "-skills/terminal-browser"]
                self.assertEqual(json.loads(settings.read_text()), expected)
                backup = settings.with_name("settings.json.before-lazy-browser")
                self.assertEqual(backup.read_text(), original)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual((override / "index.ts").read_text(), "local extension\n")
                self.assertEqual(list(external.iterdir()), [])
            self.assertFalse((override.parents[1] / "settings.json").exists())
            new = home / ".pi/profiles/new"
            new.mkdir()
            self.install(home)
            self.assertEqual((new / "extensions/lazy-capabilities").resolve(), SOURCE)

    def test_linked_invalid_and_existing_exclusions_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            linked = home / ".pi/profiles/linked"
            invalid = home / ".pi/profiles/invalid"
            ready = home / ".pi/profiles/ready"
            for profile in (linked, invalid, ready):
                profile.mkdir(parents=True)
            external = root / "settings.json"
            external.write_text('{"skills": ["local"]}\n')
            (linked / "settings.json").symlink_to(external)
            (invalid / "settings.json").write_text("[]\n")
            original = json.dumps({"skills": ["-~/.agents/skills/terminal-browser", "-skills/terminal-browser"]})
            (ready / "settings.json").write_text(original)
            for _ in range(2):
                result = self.install(home)
                self.assertIn("preserving linked settings", result.stderr)
                self.assertIn("preserving invalid settings", result.stderr)
                self.assertEqual(external.read_text(), '{"skills": ["local"]}\n')
                self.assertTrue((linked / "settings.json").is_symlink())
                self.assertEqual((invalid / "settings.json").read_text(), "[]\n")
                self.assertEqual((ready / "settings.json").read_text(), original)
                self.assertFalse((ready / "settings.json.before-lazy-browser").exists())

    def test_missing_source_does_not_modify_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            result = self.install(home, root / "missing", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing lazy-capabilities source", result.stderr)
            self.assertFalse(home.exists())

    @unittest.skipUnless(PI, "requires Pi on PATH or PI_BIN")
    def test_actual_extension_registers_loaders_in_every_profile(self):
        with tempfile.TemporaryDirectory(prefix="pi-lazy-install.") as temp:
            root = Path(temp)
            home = root / "home"
            (home / ".pi/profiles/custom").mkdir(parents=True)
            self.install(home)
            browser = home / ".agents/skills/terminal-browser"
            browser.mkdir(parents=True)
            (browser / "SKILL.md").write_text(
                "---\nname: terminal-browser\ndescription: BROWSER_CATALOG\n---\nBODY\n")
            probe = root / "probe.ts"
            probe.write_text('''import { DefaultResourceLoader, getAgentDir } from "@earendil-works/pi-coding-agent";
export default function(pi) {
  pi.on("resources_discover", async () => {
    const loader = new DefaultResourceLoader({ cwd: process.cwd(), agentDir: getAgentDir(), noExtensions: true });
    await loader.reload();
    console.error("LOADOUT " + JSON.stringify({ all: pi.getAllTools().map(t => t.name), active: pi.getActiveTools(),
      skills: loader.getSkills().skills.map(s => s.name) }));
  });
}''')
            for profile in [home / ".pi/agent", *sorted((home / ".pi/profiles").iterdir())]:
                (profile / "skills").mkdir(exist_ok=True)
                (profile / "skills/terminal-browser").symlink_to(browser)
                other = profile / "skills/unrelated"
                other.mkdir()
                (other / "SKILL.md").write_text("---\nname: unrelated\ndescription: KEEP\n---\nBODY\n")
                env = {k: v for k, v in os.environ.items() if not k.startswith("PI_")}
                env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                           PI_CODING_AGENT_DIR=str(profile), PI_TELEMETRY="0")
                result = subprocess.run([PI, "--offline", "--mode", "rpc", "--no-session", "--no-approve", "-e", str(probe)],
                                        input=b'{"type":"get_state"}\n', cwd=root, env=env,
                                        capture_output=True, check=True, timeout=20)
                loadout = next(json.loads(line[8:]) for line in result.stderr.decode().splitlines() if line.startswith("LOADOUT "))
                self.assertTrue({"codemode_enable", "wiki_enable", "browser_enable"} <= set(loadout["active"]), result.stderr)
                self.assertIn("codemode", loadout["all"])
                self.assertNotIn("codemode", loadout["active"])
                self.assertNotIn("Extension error", result.stderr.decode())
                self.assertEqual(loadout["skills"], ["unrelated"])
                self.assertTrue((profile / "skills/terminal-browser").is_symlink())
                self.assertTrue((browser / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
