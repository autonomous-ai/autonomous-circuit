"""`firmware/` is written when the Firmware tab would show something: a README and a source; built when the recipe's binary exists."""
import tempfile
import unittest
from pathlib import Path

from kicadpy import firmware


class FirmwareWrittenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)

    def write(self, rel, text='x'):
        path = self.ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_no_dir_empty_dir_or_readme_alone_is_not_written(self):
        self.assertFalse(firmware.firmware_written(self.ws))
        (self.ws / 'firmware').mkdir()
        self.assertFalse(firmware.firmware_written(self.ws))
        self.write('firmware/README.md', '# flash it')
        self.assertFalse(firmware.firmware_written(self.ws))
        self.assertEqual([g.split(' ')[0] for g in firmware.missing(self.ws)], ['firmware/', 'firmware/flash.json'])

    def test_a_source_without_a_readme_is_not_written_either(self):
        self.write('firmware/src/main.c', 'int main(void) {}')
        self.assertFalse(firmware.firmware_written(self.ws))
        self.assertEqual(len(firmware.missing(self.ws)), 2)
        self.assertIn('README.md', firmware.missing(self.ws)[0])
        self.assertIn('flash.json', firmware.missing(self.ws)[1])

    def test_readme_plus_source_is_written_and_binaries_and_build_trees_do_not_count(self):
        self.write('firmware/README.md', '# flash it')
        self.write('firmware/firmware.uf2', 'binary')
        self.write('firmware/.pio/build/main.o', 'obj')
        self.write('firmware/build/main.c', 'generated')
        self.write('firmware/.gitignore', 'build/')
        self.assertFalse(firmware.firmware_written(self.ws))
        self.write('firmware/Makefile', 'all:')
        self.assertTrue(firmware.firmware_written(self.ws))
        self.write('firmware/src/main.c', 'int main(void) {}')
        self.assertEqual(firmware.sources(self.ws), ['firmware/Makefile', 'firmware/src/main.c'])
        # Written, but the Flash button still wants its recipe — that is the next gap named.
        self.assertEqual(len(firmware.missing(self.ws)), 1)
        self.write('firmware/flash.json', '{"family": "rp2040", "uf2": "fw.uf2"}')
        self.assertTrue(firmware.firmware_written(self.ws))
        # ...and the recipe names a binary that must exist: written is not built.
        self.assertEqual(len(firmware.missing(self.ws)), 1)
        self.assertIn('firmware/fw.uf2 is missing', firmware.missing(self.ws)[0])
        self.assertFalse(firmware.firmware_built(self.ws))
        self.write('firmware/fw.uf2', 'UF2')
        self.assertEqual(firmware.missing(self.ws), [])
        self.assertTrue(firmware.firmware_built(self.ws))

    def test_built_means_the_recipe_binary_exists_and_is_current(self):
        import os, time
        self.write('firmware/README.md', '# flash it')
        self.write('firmware/src/main.c', 'int main(void) {}')
        # No recipe, or a recipe that names nothing: not built, and the gap says what is needed.
        self.assertFalse(firmware.built(self.ws))
        self.write('firmware/flash.json', '{"family": "circuitpython"}')
        self.assertIsNone(firmware.binary(self.ws))
        self.assertIn('names no binary', firmware.missing(self.ws)[0])
        # rp2040: the uf2, relative to firmware/. Empty files do not count.
        self.write('firmware/flash.json', '{"family": "rp2040", "uf2": "../build/fw.uf2"}')
        self.assertEqual(firmware.binary(self.ws), self.ws / 'build' / 'fw.uf2')
        self.write('build/fw.uf2', '')
        self.assertFalse(firmware.built(self.ws))
        self.write('build/fw.uf2', 'UF2')
        self.assertTrue(firmware.built(self.ws))
        # A source edited after the build makes it stale; a README or the recipe does not.
        later = time.time() + 60
        os.utime(self.ws / 'firmware' / 'src' / 'main.c', (later, later))
        self.assertFalse(firmware.built(self.ws))
        self.assertIn('older than the sources', firmware.missing(self.ws)[0])
        os.utime(self.ws / 'build' / 'fw.uf2', (later + 1, later + 1))
        self.assertTrue(firmware.built(self.ws))
        os.utime(self.ws / 'firmware' / 'README.md', (later + 60, later + 60))
        os.utime(self.ws / 'firmware' / 'flash.json', (later + 60, later + 60))
        self.assertTrue(firmware.built(self.ws))
        # Neither does a host helper or a host test saved after the build; a build file does.
        self.write('firmware/servoctl.py', 'print("host")')
        self.write('firmware/tests/test_proto.c', 'int main(void) {}')
        for rel in ('firmware/servoctl.py', 'firmware/tests/test_proto.c'):
            os.utime(self.ws / rel, (later + 60, later + 60))
        self.assertTrue(firmware.built(self.ws))
        self.write('firmware/CMakeLists.txt', 'project(fw)')
        os.utime(self.ws / 'firmware' / 'CMakeLists.txt', (later + 60, later + 60))
        self.assertFalse(firmware.built(self.ws))
        os.utime(self.ws / 'build' / 'fw.uf2', (later + 61, later + 61))
        self.assertTrue(firmware.built(self.ws))
        # esp32 with arduino-cli: <build>/<sketch>.ino.bin.
        self.write('firmware/flash.json', '{"family": "esp32", "tool": "arduino-cli", "sketch": "deck", "build": "../build/firmware-ssd1306"}')
        self.assertEqual(firmware.binary(self.ws), self.ws / 'build' / 'firmware-ssd1306' / 'deck.ino.bin')
        self.assertFalse(firmware.built(self.ws))
        self.write('build/firmware-ssd1306/deck.ino.bin', 'bin')
        os.utime(self.ws / 'build' / 'firmware-ssd1306' / 'deck.ino.bin', (later + 62, later + 62))
        self.assertTrue(firmware.built(self.ws))

    def test_is_source_matches_the_viewer_catalog_rules(self):
        for name in ('main.c', 'pins.h', 'main.ino', 'code.py', 'platformio.ini', 'CMakeLists.txt', 'Makefile', 'sdkconfig.defaults.yml'):
            self.assertTrue(firmware.is_source(name), name)
        for name in ('firmware.uf2', 'app.elf', 'app.bin', '.env', 'photo.png', ''):
            self.assertFalse(firmware.is_source(name), name)


if __name__ == '__main__':
    unittest.main()
