"""`firmware/` is written when the Firmware tab would show something: a README and a source."""
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
        # Written, but the Flash button still wants its recipe — that is the last gap named.
        self.assertEqual(len(firmware.missing(self.ws)), 1)
        self.write('firmware/flash.json', '{"family": "rp2040", "uf2": "fw.uf2"}')
        self.assertEqual(firmware.missing(self.ws), [])
        self.assertTrue(firmware.firmware_written(self.ws))

    def test_is_source_matches_the_viewer_catalog_rules(self):
        for name in ('main.c', 'pins.h', 'main.ino', 'code.py', 'platformio.ini', 'CMakeLists.txt', 'Makefile', 'sdkconfig.defaults.yml'):
            self.assertTrue(firmware.is_source(name), name)
        for name in ('firmware.uf2', 'app.elf', 'app.bin', '.env', 'photo.png', ''):
            self.assertFalse(firmware.is_source(name), name)


if __name__ == '__main__':
    unittest.main()
