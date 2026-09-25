import importlib.util
import sys
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_warm_runners_decode_unicode_image_paths_to_memory(tmp_path):
    image_path = tmp_path / "中文 路径" / "公式.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (3, 2), color=(12, 34, 56)).save(image_path)

    for script_name in ("_warm_paddle", "_warm_formula"):
        module = load_script(script_name)
        image = module.load_rgb_array(image_path)
        assert image.shape == (2, 3, 3)
        assert image[0, 0].tolist() == [12, 34, 56]
