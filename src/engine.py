"""剧情引擎 - 状态机驱动的剧情分支。

修复点：
1. 变量按 玩家+剧情实例 隔离，不使用任何全局状态。
2. 终态节点进入后不再参与任何转换评估。
3. 结局依据所有选择累积的分数，而非最后一次选择。
4. 回滚时恢复分支点的变量快照。
5. 过场动画与剧情状态绑定：动画结束（或跳过）后才推进节点。
6. 条件支持范围/阈值判断，浮点数使用 epsilon 比较。
7. 日志记录节点ID、变量快照与时间戳。
8. 嵌套剧情拥有独立作用域，内层写不污染外层，显式 export 才上报。
"""
import copy
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

        # bug1/bug8: 每个 (player_id, story_id) 实例独享变量字典；
        # 嵌套剧情的写入只落在自己的作用域内。
        self.variables: dict = {}
        self.current = None
        self.visited = set()
        self.terminal = set()          # bug2: 已终结的节点
        self.choice_score = 0          # bug3: 累积分数
        self.choice_weights = []       # bug3: 每次选择的权重留痕
        self.log = []                  # bug7: 节点ID + 变量快照 + 时间戳
        self._checkpoints = {}         # bug4: node_id -> 进入时的变量快照
        self._pending_cutscene = None  # bug5: (cutscene_id, 等待进入的节点)

    # ---------- 变量作用域 (bug1 / bug8) ----------

    def get_var(self, key, default=None):
        """读取变量：本作用域优先，未命中则沿外层作用域链向上查找。"""
        if key in self.variables:
            return self.variables[key]
        if self.parent is not None:
            return self.parent.get_var(key, default)
        return default

    def set_var(self, key, value):
        """写入变量：只写当前作用域，绝不污染外层剧情。"""
        self.variables[key] = value

    def export_var(self, key):
        """bug8: 显式把内层变量上报给外层；未导出的修改对外不可见。"""
        if self.parent is not None and key in self.variables:
            self.parent.set_var(key, copy.deepcopy(self.variables[key]))
            return True
        return False

    def spawn_nested(self, story_id):
        """bug8: 开启支线中的支线，新实例拥有独立变量作用域。"""
        return StoryEngine(self.player_id, story_id, parent=self)

    def _visible_vars(self):
        """条件求值可见的变量环境：外层打底，本作用域覆盖同名键。"""
        env = self.parent._visible_vars() if self.parent is not None else {}
        env.update(self.variables)
        return env

    def _snapshot(self):
        return copy.deepcopy(self.variables)

    # ---------- 条件求值 (bug6) ----------

    def evaluate_condition(self, condition):
        """bug6: 用阈值/范围助手代替裸 ==；数值比较统一走 epsilon。"""
        def eq(a, b):
            if _is_number(a) and _is_number(b):
                return abs(a - b) <= EPSILON
            return a == b

        def gt(a, b):
            return _is_number(a) and _is_number(b) and a - b > EPSILON

        def lt(a, b):
            return _is_number(a) and _is_number(b) and b - a > EPSILON

        def ge(a, b):
            return gt(a, b) or eq(a, b)

        def le(a, b):
            return lt(a, b) or eq(a, b)

        helpers = {
            "eq": eq,
            "ne": lambda a, b: not eq(a, b),
            "gt": gt,
            "ge": ge,
            "lt": lt,
            "le": le,
            "between": lambda v, lo, hi: ge(v, lo) and le(v, hi),
            "EPSILON": EPSILON,
        }
        env = dict(helpers)
        env.update(self._visible_vars())
        return bool(eval(condition, {"__builtins__": {}}, env))

    # ---------- 状态机转换 (bug2 / bug5) ----------

    def transition(self, node_id):
        """从当前节点发起转换；终态节点与已终结的剧情都不再评估。"""
        if node_id in self.terminal:
            return None
        if self.current is not None and self.current in self.terminal:
            return None
        node = self.nodes[node_id]
        if node.cutscene:
            # bug5: 过场未结束前剧情停在原节点，仅登记待进入节点。
            self._pending_cutscene = (node.cutscene, node_id)
            return None
        return self._enter(node_id)

    def _enter(self, node_id):
        node = self.nodes[node_id]
        if node.condition is not None and not self.evaluate_condition(node.condition):
            return None
        self.current = node_id
        self.visited.add(node_id)
        self._checkpoints[node_id] = self._snapshot()  # bug4
        self.log.append({                              # bug7
            "node": node_id,
            "variables": self._snapshot(),
            "timestamp": time.time(),
        })
        if node.is_end:
            self.terminal.add(node_id)                 # bug2
        return node

    def cutscene_finished(self, skipped=False):
        """bug5: 动画正常播完或被跳过，都会推进到挂起的剧情节点。"""
        if self._pending_cutscene is None:
            return None
        _, node_id = self._pending_cutscene
        self._pending_cutscene = None
        return self._enter(node_id)

    def skip_cutscene(self):
        return self.cutscene_finished(skipped=True)

    # ---------- 选择与结局 (bug3) ----------

    def make_choice(self, choice_idx):
        if self._pending_cutscene is not None:
            return None
        node = self.nodes[self.current]
        choice = node.choices[choice_idx]
        weight = choice.get("score", 0)
        self.choice_score += weight            # bug3: 累积，不覆盖
        self.choice_weights.append(weight)
        for key, value in choice.get("set", {}).items():
            self.set_var(key, value)
        if "next" in choice:
            return self.transition(choice["next"])
        return None

    def determine_ending(self):
        """bug3: 全部选择的累积分数决定结局。"""
        if self.choice_score > 0:
            return "good"
        if self.choice_score < 0:
            return "bad"
        return "neutral"

    # ---------- 回滚 (bug4) ----------

    def rollback(self, node_id):
        """bug4: 读档回到分支点，并把变量恢复为进入该节点时的快照。"""
        if node_id in self._checkpoints:
            self.variables = self._checkpoints[node_id]
        self.current = node_id
        self.terminal.discard(node_id)
        self._pending_cutscene = None
        return self.nodes.get(node_id)
