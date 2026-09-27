"""剧情引擎测试"""
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.engine import StoryEngine, StoryNode

class TestVariables:
    def test_variables_isolated(self):
        se1 = StoryEngine()
        se2 = StoryEngine()
        se1.variables["trust"] = 1
        assert "trust" not in se2.variables or se2.variables.get("trust") != 1, "变量全局共享"

class TestEnding:
    def test_ending_uses_all_choices(self):
        se = StoryEngine()
        se.nodes["start"] = StoryNode("start", "s", choices=[{"text":"a","score":5,"next":"mid"}])
        se.nodes["mid"] = StoryNode("mid", "m", choices=[{"text":"b","score":-10,"next":"end"}], is_end=False)
        se.nodes["end"] = StoryNode("end", "e", is_end=True)
        se.transition("start")
        se.make_choice(0)
        se.make_choice(0)
        ending = se.determine_ending()
        # 5 + (-10) = -5, 应该bad
        assert ending == "bad", f"结局只看最后选择: {ending}"
