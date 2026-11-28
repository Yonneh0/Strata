"""The facts the Monitor's cards ask the machine for themselves: the memory sticks, and the processor's temperature.

memory_facts() is the firmware's answer through PowerShell, parsed here from rows measured on the fleet: a stick alone
arrives as a bare object rather than a list, a slot named "DIMM 0" says nothing about channels and gets none claimed,
and a mixed kit is not "N x the biggest stick".  CpuTemp is the reading, the source that gave it, and the rule that a
machine with neither stops being asked.  The sampler asks for the sticks once and never changes its mind - unless WMI
had not finished starting when it first asked, which is what a task started by the machine itself meets.

    python -m unittest serve.test_telemetry_facts
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from serve.telemetry import (CpuTemp, Telemetry, linux_cpu_temp, memory_facts, plausible_c)


class Out:
    """What subprocess.run answers for the PowerShell the facts are asked through."""

    def __init__(self, stdout):
        self.stdout = stdout


def ask(*rows):
    return memory_facts(run=lambda *a, **k: Out(json.dumps(list(rows) if len(rows) > 1 else rows[0])))


MSI = [{"Capacity": 34359738368, "ConfiguredClockSpeed": 2667, "Speed": 3200,
        "DeviceLocator": "ChannelA-DIMM0", "SMBIOSMemoryType": 26},
       {"Capacity": 34359738368, "ConfiguredClockSpeed": 2667, "Speed": 3200,
        "DeviceLocator": "ChannelB-DIMM0", "SMBIOSMemoryType": 26}]

# a laptop's firmware (measured on the X1): both sticks answer "DIMM 0", which names no channel
X1 = [{"Capacity": 51539607552, "ConfiguredClockSpeed": 5600, "Speed": 5600,
       "DeviceLocator": "DIMM 0", "SMBIOSMemoryType": 34}] * 2


class MemoryFacts(unittest.TestCase):
    def test_the_sticks_a_firmware_names_by_channel_come_out_as_both_channels(self):
        self.assertEqual(ask(*MSI), {"ram_sticks": 2, "ram_stick_gibs": [32, 32], "ram_stick_gib": 32,
                                     "ram_type": "DDR4", "ram_mt_s": 2667, "ram_rated_mt_s": 3200,
                                     "ram_channels": 2})

    def test_one_stick_arrives_as_an_object_and_not_a_list(self):
        got = ask(MSI[0])
        self.assertEqual(got["ram_sticks"], 1)
        self.assertEqual(got["ram_channels"], 1)

    def test_a_firmware_that_names_no_channel_gets_none_claimed(self):
        self.assertNotIn("ram_channels", ask(*X1), "both sticks say DIMM 0: that is a slot, not a channel")

    def test_a_mixed_kit_is_not_printed_as_the_biggest_stick_times_everything(self):
        small, big = dict(MSI[0], Capacity=17179869184), MSI[1]
        got = ask(small, big)
        self.assertIsNone(got["ram_stick_gib"], "there is no size every stick has")
        self.assertEqual(got["ram_stick_gibs"], [16, 32], "the sizes themselves, for the tooltip to tell")
        self.assertEqual(got["ram_sticks"], 2)

    def test_the_kinds_smbios_numbers(self):
        for code, kind in [(18, "DDR"), (19, "DDR2"), (24, "DDR3"), (26, "DDR4"), (34, "DDR5")]:
            with self.subTest(code=code):
                self.assertEqual(ask(dict(MSI[0], SMBIOSMemoryType=code))["ram_type"], kind)
        self.assertIsNone(ask(dict(MSI[0], SMBIOSMemoryType=0))["ram_type"], "a kind nobody has heard of is no kind")

    def test_nothing_is_made_of_nothing(self):
        empty = memory_facts(run=lambda *a, **k: Out(""))
        self.assertEqual(empty, {})
        for junk in ("", "   ", "not json at all", "[]", '[{"ConfiguredClockSpeed": 2667}]'):
            with self.subTest(junk=junk):
                self.assertEqual(memory_facts(run=lambda *a, _j=junk, **k: Out(_j)), {},
                                 "a row without a Capacity says nothing about a stick")

    def test_a_source_that_raises_leaves_the_card_blank_and_the_server_alone(self):
        def boom(*a, **k):
            raise OSError("winmgmt is not answering")
        self.assertEqual(memory_facts(run=boom), {})


class AskMemFacts(unittest.TestCase):
    """The sampler's rule for asking: once, and a few times more when WMI had not finished starting."""

    def drive(self, answers, nt=True):
        seen = SimpleNamespace(mem_facts=None, mem_next=0.0, mem_tries=0)
        asked, clock = [], SimpleNamespace(now=0.0)

        def facts():
            asked.append(clock.now)
            return answers[len(asked) - 1] if len(asked) <= len(answers) else {}

        with mock.patch("serve.telemetry.memory_facts", facts), \
             mock.patch("serve.telemetry.os.name", "nt" if nt else "posix"):
            for clock.now in (0.0, 10.0, 30.0, 45.0, 60.0, 500.0):
                Telemetry._ask_mem_facts(seen, clock.now)
        return seen, asked

    def test_an_answer_is_the_end_of_the_asking(self):
        seen, asked = self.drive([{"ram_sticks": 2}])
        self.assertEqual(asked, [0.0], "a machine that answered is not asked again")
        self.assertEqual(seen.mem_facts, {"ram_sticks": 2})

    def test_silence_is_gone_through_three_times_before_it_is_accepted(self):
        seen, asked = self.drive([])
        self.assertEqual(asked, [0.0, 30.0, 60.0], "the samples at 10 s and 45 s come too soon after the one before: "
                                                "they wait out the 30 s instead of waking WMI for nothing")
        self.assertEqual(seen.mem_facts, {}, "and then the card prints nothing, with no asking left")

    def test_a_firmware_that_clears_only_its_throat_still_gets_its_facts(self):
        seen, asked = self.drive([{}, {"ram_sticks": 2}])
        self.assertEqual(seen.mem_facts, {"ram_sticks": 2})
        self.assertEqual(asked, [0.0, 30.0])

    def test_linux_is_never_asked_at_all(self):
        seen, asked = self.drive([], nt=False)
        self.assertEqual(asked, [], "the sticks are behind root there: the question is not worth asking")
        self.assertEqual(seen.mem_facts, {})


class Plausible(unittest.TestCase):
    def test_a_temperature_the_page_would_print(self):
        for v, ok in [(0.0, False), (-3.0, False), (150.0, False), (149.9, True), (46.2, True), (None, False)]:
            with self.subTest(v=v):
                self.assertEqual(bool(plausible_c(v)), ok)


class CpuFromHwmon(unittest.TestCase):
    """The kernel's chips, from a fixture of one: the hottest of the processor's own, and nothing else's."""

    def fixture(self, root):
        # a chip's own ceiling is not a reading; a disk is not a processor; a chip with no files has nothing to say
        for name, inputs in [("k10temp", {"temp1_input": "52500", "temp2_input": "49000", "temp3_input": "150000"}),
                             ("nvme", {"temp1_input": "39000"}),
                             ("coretemp", {})]:
            os.mkdir(os.path.join(root, name))
            with open(os.path.join(root, name, "name"), "w", encoding="utf-8") as f:
                f.write(name + "\n")
            for fn, value in inputs.items():
                with open(os.path.join(root, name, fn), "w", encoding="utf-8") as f:
                    f.write(value + "\n")

    def test_the_hottest_of_the_processors_chips(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            self.assertEqual(linux_cpu_temp(root), 52.5)

    def test_no_fixture_no_reading(self):
        self.assertIsNone(linux_cpu_temp("/definitely/not/here"))
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "k10temp"))   # a chip whose files vanished between two reads
            self.assertIsNone(linux_cpu_temp(root))


class CpuTempSource(unittest.TestCase):
    # CpuTemp builds the machine's own counter when it is given none, so a test that wants no counter has to say so:
    # the real one answers on a Windows, and those tests would watch this desk's sensors instead of their own.
    NO_COUNTER = SimpleNamespace(off=True, read=lambda: None)

    def test_the_counter_that_answers_is_named_as_the_counter(self):
        c = CpuTemp(win=lambda: 60.0, lin=lambda: 60.0, pdh=SimpleNamespace(off=False, read=lambda: 71.5))
        self.assertEqual(c.read(), 71.5)
        self.assertEqual(c.src, "acpi zone", "a thermal zone's degrees are the zone's, and the page has to be able to say so")

    def test_the_sensor_that_answers_is_named_as_the_sensor(self):
        c = CpuTemp(win=lambda: 60.0, lin=lambda: 52.5, pdh=self.NO_COUNTER)
        with mock.patch("serve.telemetry.os.name", "posix"):      # which source is asked is the machine's operating
            got = [c.read(), c.read()]                            # system's choice, so the test says which machine
        self.assertEqual(got, [52.5, 52.5])
        self.assertEqual(c.src, "hwmon")

    def test_the_answer_stands_between_two_asks(self):
        asked = []
        c = CpuTemp(win=lambda: 60.0, lin=lambda: (asked.append(1), 52.5)[1], pdh=self.NO_COUNTER)
        with mock.patch("serve.telemetry.os.name", "posix"):
            c.read()
            for _ in range(5):
                c.read()
        self.assertEqual(len(asked), 1, "the card is refreshed every second; the sensor is asked every ten")

    def test_a_machine_with_neither_stops_being_asked(self):
        asked = []
        c = CpuTemp(win=lambda: asked.append(1) or None, lin=None, pdh=self.NO_COUNTER)
        with mock.patch("serve.telemetry.os.name", "nt"):     # a silent thermal zone is a Windows answer, not a Linux one
            for _ in range(5):
                c.next = 0.0            # the gap between two asks, passed without waiting for the clock
                c.read()
        self.assertTrue(c.off)
        self.assertEqual(len(asked), CpuTemp.WIN_MAX_TRIES, "three, and then the silence is this machine's answer")

    def test_a_source_that_raises_is_survived(self):
        def boom():
            raise RuntimeError("no WMI today")
        c = CpuTemp(win=boom, lin=boom, pdh=self.NO_COUNTER)
        c.next = 0.0
        self.assertIsNone(c.read())


if __name__ == "__main__":
    unittest.main()
