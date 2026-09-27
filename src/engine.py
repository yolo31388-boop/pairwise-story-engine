"""剧情引擎 - 状态机驱动的剧情分支"""
import time
from dataclasses import dataclass, field

EPSILON = 1e-9


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class StoryNode:
    nid: str
    text: str
    choices: list = field(default_factory=list)
    condition: str = None
    is_end: bool = False
    cutscene: str = None


class StoryEngine:
    def __init__(self, player_id="default", story_id="main", parent=None):
        self.player_id = player_id
        self.story_id = story_id
        self.parent = parent
        self.nodes: dict[str, StoryNode] = {}
        # bug1/bug8: 变量按玩家+剧情实例隔离；嵌套剧情写操作只落在本作用域
        self.variables: dict = {}
        self.current = None
        self.visited = set()
        self.terminal = set()          # bug2: 终态节点集合
        self.choice_score = 0          # bug3: 累积所有选择的分数
        self.log = []                  # bug7: 节点ID + 变量快照 + 时间戳
        self._checkpoints = {}         # bug4: 分支点变量快照
        self._pending_cutscene = None  # bug5: (cutscene_id, 待进入节点)

    # ---------- 变量作用域 (bug1 / bug8) ----------

    def get_var(self, key, default=None):
        """读变量：本作用域优先，未命中时沿外层作用域向上查找。"""
        if key in self.variables:
            return self.variables[key]
        if self.parent is not None:
            return self.parent.get_var(key, default)
        return default

    def set_var(self, key, value):
        """写变量：只写本作用域，不污染外层。"""
        self.variables[key] = value

    def export_var(self, key):
        """bug8: 显式把本作用域变量导出到外层，否则内层修改不影响外层。"""
        if self.parent is not None and key in self.variables:
            self.parent.set_var(key, self.variables[key])

    def spawn_nested(self, story_id):
        """bug8: 创建嵌套剧情，独立作用域。"""
        return StoryEngine(player_id=self.player_id, story_id=story_id, parent=self)

    def _visible_vars(self):
        env = self.parent._visible_vars() if self.parent is not None else {}
        env.update(self.variables)
        return env

    # ---------- 条件求值 (bug6) ----------

    def evaluate_condition(self, condition):
        """bug6: 浮点用 epsilon 比较，提供范围/阈值判断助手，禁用精确等于的裸比较。"""
        def eq(a, b):
            if _is_number(a) and _is_number(b):
                return abs(a - b) <= EPSILON
            return a == b

        def ge(a, b):
            return _is_number(a) and _is_number(b) and (a - b > EPSILON or eq(a, b))

        def le(a, b):
            return _is_number(a) and _is_number(b) and (b - a > EPSILON or eq(a, b))

        helpers = {
            "eq": eq,
            "ne": lambda a, b: not eq(a, b),
            "gt": lambda a, b: _is_number(a) and _is_number(b) and a - b > EPSILON,
            "ge": ge,
            "lt": lambda a, b: _is_number(a) and _is_number(b) and b - a > EPSILON,
            "le": le,
            "between": lambda v, lo, hi: ge(v, lo) and le(v, hi),
            "EPSILON": EPSILON,
        }
        env = dict(helpers)
        env.update(self._visible_vars())
        return bool(eval(condition, {"__builtins__": {}}, env))

    # ---------- 状态机转换 (bug2 / bug5) ----------

    def transition(self, node_id):
        """bug2: 终态节点不再评估；bug5: 带过场的节点等动画结束才进入。"""
        if node_id in self.terminal:
            return None
        if self.current is not None and self.current in self.terminal:
            return None
        node = self.nodes[node_id]
        if node.cutscene:
            self._pending_cutscene = (node.cutscene, node_id)
            return None
        return self._enter(node_id)

    def _enter(self, node_id):
        node = self.nodes[node_id]
        if node.condition is not None and not self.evaluate_condition(node.condition):
            return None
        self.current = node_id
        self.visited.add(node_id)
        self._checkpoints[node_id] = dict(self.variables)  # bug4
        self.log.append({                                  # bug7
            "node": node_id,
            "variables": dict(self.variables),
            "timestamp": time.time(),
        })
        if node.is_end:
            self.terminal.add(node_id)                     # bug2
        return node

    def cutscene_finished(self, skipped=False):
        """bug5: 过场动画结束（或被跳过）才推进剧情节点。"""
        if self._pending_cutscene is None:
            return None
        _, node_id = self._pending_cutscene
        self._pending_cutscene = None
        return self._enter(node_id)

    def skip_cutscene(self):
        return self.cutscene_finished(skipped=True)

    # ---------- 选择与结局 (bug3) ----------

    def make_choice(self, choice_idx):
        node = self.nodes[self.current]
        choice = node.choices[choice_idx]
        self.choice_score += choice.get("score", 0)  # bug3: 累积而非覆盖
        for key, value in choice.get("set", {}).items():
            self.set_var(key, value)
        if "next" in choice:
            return self.transition(choice["next"])
        return None

    def determine_ending(self):
        """bug3: 用累积分数判定结局，所有选择都影响结局。"""
        if self.choice_score > 0:
            return "good"
        if self.choice_score < 0:
            return "bad"
        return "neutral"

    # ---------- 回滚 (bug4) ----------

    def rollback(self, node_id):
        """bug4: 回到分支点并恢复当时的变量快照。"""
        if node_id in self._checkpoints:
            self.variables = dict(self._checkpoints[node_id])
        self.current = node_id
        self.terminal.discard(node_id)
        self._pending_cutscene = None
