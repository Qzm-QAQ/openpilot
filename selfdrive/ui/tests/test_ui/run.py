import pathlib
import shutil
import jinja2
import matplotlib.pyplot as plt
import numpy as np
import os
import pyautogui
import pywinctl
import time
import unittest

from parameterized import parameterized
from cereal import messaging, car, log
from cereal.visionipc import VisionIpcServer, VisionStreamType

from cereal.messaging import SubMaster, PubMaster
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.transformations.camera import tici_f_frame_size
from openpilot.selfdrive.test.helpers import with_processes
from openpilot.selfdrive.test.process_replay.vision_meta import meta_from_camera_state
from openpilot.tools.webcam.camera import Camera

UI_DELAY = 0.5 # may be slower on CI?

NetworkType = log.DeviceState.NetworkType
NetworkStrength = log.DeviceState.NetworkStrength

EventName = car.CarEvent.EventName
EVENTS_BY_NAME = {v: k for k, v in EventName.schema.enumerants.items()}


def setup_common(click, pm: PubMaster):
  Params().put("DongleId", "123456789012345")
  dat = messaging.new_message('deviceState')
  dat.deviceState.started = True
  dat.deviceState.networkType = NetworkType.cell4G
  dat.deviceState.networkStrength = NetworkStrength.moderate
  dat.deviceState.freeSpacePercent = 80
  dat.deviceState.memoryUsagePercent = 2
  dat.deviceState.cpuTempC = [2,]*3
  dat.deviceState.gpuTempC = [2,]*3
  dat.deviceState.cpuUsagePercent = [2,]*8

  pm.send("deviceState", dat)

  time.sleep(UI_DELAY)

def setup_homescreen(click, pm: PubMaster):
  setup_common(click, pm)

def setup_settings_device(click, pm: PubMaster):
  setup_common(click, pm)

  click(100, 100)
  time.sleep(UI_DELAY)

def setup_settings_network(click, pm: PubMaster):
  setup_common(click, pm)

  setup_settings_device(click, pm)
  click(300, 600)
  time.sleep(UI_DELAY)

def setup_onroad(click, pm: PubMaster):
  setup_common(click, pm)

  dat = messaging.new_message('pandaStates', 1)
  dat.pandaStates[0].ignitionLine = True
  dat.pandaStates[0].pandaType = log.PandaState.PandaType.uno

  pm.send("pandaStates", dat)

  server = VisionIpcServer("camerad")
  server.create_buffers(VisionStreamType.VISION_STREAM_ROAD, 40, False, *tici_f_frame_size)
  server.create_buffers(VisionStreamType.VISION_STREAM_DRIVER, 40, False, *tici_f_frame_size)
  server.create_buffers(VisionStreamType.VISION_STREAM_WIDE_ROAD, 40, False, *tici_f_frame_size)
  server.start_listener()

  time.sleep(UI_DELAY)

  IMG = Camera.bgr2nv12(np.random.randint(0, 255, (*tici_f_frame_size,3), dtype=np.uint8))
  IMG_BYTES = IMG.flatten().tobytes()

  cams = ('roadCameraState', 'wideRoadCameraState')

  frame_id = 0
  for cam in cams:
    msg = messaging.new_message(cam)
    cs = getattr(msg, cam)
    cs.frameId = frame_id
    cs.timestampSof = int((frame_id * DT_MDL) * 1e9)
    cs.timestampEof = int((frame_id * DT_MDL) * 1e9)
    cam_meta = meta_from_camera_state(cam)

    pm.send(msg.which(), msg)
    server.send(cam_meta.stream, IMG_BYTES, cs.frameId, cs.timestampSof, cs.timestampEof)

  time.sleep(UI_DELAY)

def setup_onroad_map(click, pm: PubMaster):
  setup_onroad(click, pm)
  click(500, 500)
  time.sleep(UI_DELAY)

def setup_onroad_sidebar(click, pm: PubMaster):
  setup_onroad_map(click, pm)
  click(500, 500)
  time.sleep(UI_DELAY)

CASES = {
  "homescreen": setup_homescreen,
  "settings_device": setup_settings_device,
  "settings_network": setup_settings_network,
  "onroad": setup_onroad,
  "onroad_map": setup_onroad_map,
  "onroad_map_sidebar": setup_onroad_sidebar
}

TEST_DIR = pathlib.Path(__file__).parent

TEST_OUTPUT_DIR = TEST_DIR / "report"
SCREENSHOTS_DIR = TEST_OUTPUT_DIR / "screenshots"


class TestUI(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    os.environ["SCALE"] = "1"

  def setup(self):
    self.sm = SubMaster(["uiDebug"])
    self.pm = PubMaster(["deviceState", "pandaStates", "controlsState", 'roadCameraState', 'wideRoadCameraState'])
    while not self.sm.valid["uiDebug"]:
      self.sm.update(1)
    time.sleep(UI_DELAY) # wait a bit more for the UI to finish rendering
    self.ui = pywinctl.getWindowsWithTitle("ui")[0]

  def screenshot(self):
    im = pyautogui.screenshot(region=(self.ui.left, self.ui.top, self.ui.width, self.ui.height))
    self.assertEqual(im.width, 2160)
    self.assertEqual(im.height, 1080)
    img = np.array(im)
    im.close()
    return img

  def click(self, x, y, *args, **kwargs):
    pyautogui.click(self.ui.left + x, self.ui.top + y, *args, **kwargs)

  @parameterized.expand(CASES.items())
  @with_processes(["ui"])
  def test_ui(self, name, setup_case):
    self.setup()

    setup_case(self.click, self.pm)

    im = self.screenshot()
    plt.imsave(SCREENSHOTS_DIR / f"{name}.png", im)


def create_html_report():
  OUTPUT_FILE = TEST_OUTPUT_DIR / "index.html"

  with open(TEST_DIR / "template.html") as f:
    template = jinja2.Template(f.read())

  cases = {f.stem: (str(f.relative_to(TEST_OUTPUT_DIR)), "reference.png") for f in SCREENSHOTS_DIR.glob("*.png")}
  cases = dict(sorted(cases.items()))

  with open(OUTPUT_FILE, "w") as f:
    f.write(template.render(cases=cases))

def create_screenshots():
  if TEST_OUTPUT_DIR.exists():
    shutil.rmtree(TEST_OUTPUT_DIR)

  SCREENSHOTS_DIR.mkdir(parents=True)
  unittest.main(exit=False)

if __name__ == "__main__":
  print("creating test screenshots")
  create_screenshots()

  print("creating html report")
  create_html_report()
