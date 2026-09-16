import streamlit as st
import random
import time
from dataclasses import dataclass, field
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components

# ============================================================
# 页面伪装配置
# ============================================================
st.set_page_config(
    page_title="项目进度同步会",
    layout="wide",
)

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

html, body, [class*="css"] { color: #000 !important; }
.stApp { background-color: #fff !important; }

/* ---------- 所有按钮统一白底黑字黑边框 ---------- */
.stButton > button,
.stButton > button:focus,
.stButton > button:active,
.stButton > button[kind="primary"],
.stButton > button[kind="secondary"],
.stButton > button[data-testid="baseButton-primary"],
.stButton > button[data-testid="baseButton-secondary"] {
    background-color: #fff !important;
    color: #000 !important;
    border: 1px solid #000 !important;
    box-shadow: none !important;
}
.stButton > button:hover,
.stButton > button[kind="primary"]:hover,
.stButton > button[kind="secondary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover,
.stButton > button[data-testid="baseButton-secondary"]:hover {
    background-color: #f0f0f0 !important;
    color: #000 !important;
    border: 1px solid #000 !important;
}
.stButton > button:disabled,
.stButton > button:disabled:hover {
    background-color: #f5f5f5 !important;
    color: #999 !important;
    border: 1px solid #ccc !important;
}

/* ---------- 提示框统一灰阶 ---------- */
div[data-testid="stAlert"] {
    background-color: #f5f5f5 !important;
    color: #000 !important;
    border: 1px solid #999 !important;
    border-radius: 4px !important;
}
div[data-testid="stAlert"] * { color: #000 !important; }
.stAlert svg { fill: #000 !important; color: #000 !important; }

/* ---------- 抢盖阶段专用提示：加粗黑框 ---------- */
div[data-testid="stAlert"].snap-alert {
    background-color: #fff !important;
    border: 2px solid #000 !important;
    font-weight: bold !important;
}

h1, h2, h3, h4, h5, h6 { color: #000 !important; }

input, textarea, select {
    background-color: #fff !important;
    color: #000 !important;
    border: 1px solid #999 !important;
}
div[role="radiogroup"] label { color: #000 !important; }
hr { border-color: #ccc !important; }
</style>
""", unsafe_allow_html=True)

st.title("项目进度同步会")

# ============================================================
# 访问凭证
# ============================================================
INVITE_CODE = "1122"

if "invited" not in st.session_state:
    st.session_state.invited = False

if not st.session_state.invited:
    st.divider()
    st.subheader("请输入访问凭证")
    code_input = st.text_input("访问凭证", type="password", key="invite_input")
    if st.button("进入", key="invite_submit"):
        if code_input == INVITE_CODE:
            st.session_state.invited = True
            st.rerun()
        else:
            st.error("访问凭证不正确")
    st.stop()

# ============================================================
# 常量
# ============================================================
AI_MIN_DELAY = 0.4
AI_MAX_DELAY = 2.5
HUMAN_TIMEOUT = 10.0        # 人类总时限（秒）
RESOLVE_GRACE = 2.0         # 结算后的宽限期，避免延迟点击被判误盖
AI_MISFIRE_RATE = 0.004
REFRESH_MS = 500

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
RANK_VALUE = {r: i + 1 for i, r in enumerate(RANKS)}
SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def card_to_symbol(card):
    rank, suit = card.split("-")
    return f"{rank}{SUIT_SYMBOL[suit]}"


# ============================================================
# 数据模型
# ============================================================
@dataclass
class SnapPlayer:
    player_id: str
    hand: list = field(default_factory=list)
    is_ai: bool = False
    snap_pressed: bool = False
    snap_time: float = 0.0
    ai_snap_delay: float = 0.0
    last_action: str = ""


class SnapGame:
    def __init__(self, num_players: int):
        self.seats = [SnapPlayer(player_id="我")]
        ai_names = ["A001", "A002", "A003"]
        for i in range(num_players - 1):
            self.seats.append(SnapPlayer(player_id=ai_names[i], is_ai=True))
        self.max_players = num_players

        self.center_pile = []
        self.current_index = 0
        self.current_number = 1
        self.phase = "playing"
        self.snap_start_time = 0.0
        self.last_snap_resolve_time = 0.0   # 上一次抢盖结算的时间
        self.last_result = None
        self.loser_id = None
        self.next_ai_play_time = 0.0
        self.last_played_card = None
        self.last_played_number = 0
        self.last_player_id = None

    def start(self):
        deck = [f"{r}-{s}" for r in RANKS for s in SUITS]
        assert len(deck) == len(set(deck)), "牌堆出现重复！"
        random.shuffle(deck)
        n = len(self.seats)

        for p in self.seats:
            p.hand = []
            p.snap_pressed = False
            p.snap_time = 0.0
            p.ai_snap_delay = 0.0
            p.last_action = ""

        for i, c in enumerate(deck):
            self.seats[i % n].hand.append(c)

        self.center_pile = []
        self.current_number = 1
        self.current_index = random.randrange(n)
        self.phase = "playing"
        self.last_snap_resolve_time = 0.0
        self.last_result = None
        self.loser_id = None
        self.last_played_card = None
        self.last_played_number = 0
        self.last_player_id = None
        self._set_ai_timer()

    def _set_ai_timer(self):
        seat = self.seats[self.current_index]
        if seat.is_ai:
            self.next_ai_play_time = time.time() + random.uniform(0.8, 1.4)

    def _next_player_with_cards(self, from_idx):
        for offset in range(1, self.max_players + 1):
            idx = (from_idx + offset) % self.max_players
            if self.seats[idx].hand:
                return idx
        return -1

    def _check_game_over(self):
        with_cards = [s for s in self.seats if s.hand]
        if len(with_cards) == 0:
            self.phase = "finished"
            self.loser_id = self.last_player_id
            return True
        if len(with_cards) == 1:
            self.phase = "finished"
            self.loser_id = with_cards[0].player_id
            return True
        return False

    def play(self):
        seat = self.seats[self.current_index]
        if not seat.hand:
            return

        card = seat.hand.pop(0)
        self.center_pile.append(card)
        rank_val = RANK_VALUE[card.split("-")[0]]

        self.last_played_card = card
        self.last_played_number = self.current_number
        self.last_player_id = seat.player_id
        seat.last_action = f"提交 {card_to_symbol(card)}（编号 {self.current_number}）"

        if rank_val == self.current_number:
            self._start_snapping()
            return

        self.current_number = self.current_number % 13 + 1

        if self._check_game_over():
            return

        nxt = self._next_player_with_cards(self.current_index)
        if nxt < 0:
            self.phase = "finished"
            return
        self.current_index = nxt
        self._set_ai_timer()

    def _start_snapping(self):
        self.phase = "snapping"
        self.snap_start_time = time.time()
        for s in self.seats:
            s.snap_pressed = False
            s.snap_time = 0.0
            if s.is_ai and s.hand:
                s.ai_snap_delay = random.uniform(AI_MIN_DELAY, AI_MAX_DELAY)
            else:
                s.ai_snap_delay = 0.0

    def update_snapping(self):
        if self.phase != "snapping":
            return
        elapsed = time.time() - self.snap_start_time
        for s in self.seats:
            if not s.is_ai or s.snap_pressed or not s.hand:
                continue
            if elapsed >= s.ai_snap_delay:
                s.snap_pressed = True
                s.snap_time = self.snap_start_time + s.ai_snap_delay
                s.last_action = "已确认"

    def press_snap(self, player):
        if player is None or not player.hand:
            return

        if self.phase == "snapping":
            if player.snap_pressed:
                return
            player.snap_pressed = True
            player.snap_time = time.time()
            player.last_action = "已确认"
            return

        if self.phase == "playing":
            # 宽限期：如果 2 秒内刚结算过抢盖，这次点击视为延迟点击，忽略
            if time.time() - self.last_snap_resolve_time < RESOLVE_GRACE:
                return
            self._misfire(player)

    def _misfire(self, player):
        if not self.center_pile:
            player.last_action = "误确认（中央为空）"
            return

        taken = list(self.center_pile)
        player.hand.extend(taken)
        self.center_pile = []
        self.last_result = {
            "loser_id": player.player_id,
            "taken_count": len(taken),
            "reason": "misfire",
            "timestamp": time.time(),
        }
        self.loser_id = player.player_id
        player.last_action = f"误确认，带走 {len(taken)} 张"

        if self._check_game_over():
            return

        if not self.seats[self.current_index].hand:
            nxt = self._next_player_with_cards(self.current_index)
            if nxt < 0:
                self.phase = "finished"
                return
            self.current_index = nxt
        self._set_ai_timer()

    def update_ai_misfire(self):
        if self.phase != "playing":
            return
        if not self.center_pile:
            return
        for s in self.seats:
            if not s.is_ai or not s.hand:
                continue
            if random.random() < AI_MISFIRE_RATE:
                self._misfire(s)
                return

    def resolve_snapping(self):
        participants = [s for s in self.seats if s.hand]
        if not participants:
            self.phase = "playing"
            return

        slowest = None
        slowest_time = -1.0
        for s in participants:
            t = s.snap_time if s.snap_pressed else float("inf")
            if t > slowest_time:
                slowest_time = t
                slowest = s

        taken = list(self.center_pile)
        slowest.hand.extend(taken)
        self.center_pile = []

        self.last_snap_resolve_time = time.time()   # 记录结算时间
        self.last_result = {
            "loser_id": slowest.player_id,
            "taken_count": len(taken),
            "reason": "slowest",
            "timestamp": time.time(),
        }
        self.loser_id = slowest.player_id

        for s in self.seats:
            s.snap_pressed = False
            s.snap_time = 0.0

        if self._check_game_over():
            return

        loser_idx = self.seats.index(slowest)
        self.current_number = 1
        self.phase = "playing"

        nxt = self._next_player_with_cards(loser_idx)
        if nxt < 0:
            self.phase = "finished"
            return
        self.current_index = nxt
        self._set_ai_timer()


# ============================================================
# 自动刷新
# ============================================================
if st.session_state.get("game") is not None:
    st_autorefresh(interval=REFRESH_MS, key="refresh")

# ============================================================
# 开始设置
# ============================================================
if "game" not in st.session_state:
    st.session_state.game = None

if st.session_state.game is None:
    st.subheader("开始设置")

    n = st.radio(
        "参与人数（你 + AI）",
        [2, 3, 4],
        horizontal=True,
        index=1,
    )

    st.caption("你固定坐在 1 号位，其余为 AI 模拟账户。")
    st.caption("盖牌按钮全程可用——对上了再按，按错要罚。空格键也可以盖牌。")

    if st.button("开始游戏", use_container_width=True):
        g = SnapGame(n)
        g.start()
        st.session_state.game = g
        st.rerun()

    st.stop()

game = st.session_state.game

# ============================================================
# 每帧逻辑更新
# ============================================================
game.update_snapping()
game.update_ai_misfire()

# ---------- 抢盖结算 ----------
if game.phase == "snapping":
    elapsed = time.time() - game.snap_start_time
    participants = [s for s in game.seats if s.hand]
    all_snapped = len(participants) > 0 and all(s.snap_pressed for s in participants)

    ai_waiting = any(s.is_ai and s.hand and not s.snap_pressed for s in game.seats)
    human = game.seats[0]
    human_waiting = bool(human.hand) and not human.snap_pressed

    should_resolve = False
    if all_snapped:
        should_resolve = True
    elif not ai_waiting and not human_waiting:
        should_resolve = True
    elif elapsed >= HUMAN_TIMEOUT:
        should_resolve = True

    if should_resolve:
        game.resolve_snapping()
        st.rerun()

if game.phase == "playing":
    seat = game.seats[game.current_index]
    if seat.is_ai and time.time() >= game.next_ai_play_time:
        game.play()
        st.rerun()

# ============================================================
# 空格键监听
# ============================================================
components.html(
    """
    <script>
    (function() {
        function clickSnap() {
            try {
                var doc = window.parent.document;
                var buttons = doc.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var txt = buttons[i].innerText || '';
                    if (txt.indexOf('盖牌') !== -1 && !buttons[i].disabled) {
                        buttons[i].click();
                        return true;
                    }
                }
            } catch (e) {}
            return false;
        }
        function onKey(e) {
            if (e.code === 'Space' || e.key === ' ' || e.keyCode === 32) {
                var t = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
                if (t === 'input' || t === 'textarea' || t === 'select') return;
                e.preventDefault();
                e.stopPropagation();
                clickSnap();
            }
        }
        if (!window.parent.__snapKeyBound) {
            window.parent.__snapKeyBound = true;
            window.parent.document.addEventListener('keydown', onKey, true);
        }
    })();
    </script>
    """,
    height=0,
)

# ============================================================
# 顶部信息
# ============================================================
top_a, top_b = st.columns([4, 1])
with top_a:
    st.caption("单人模拟模式（对方为 AI 账户）")
with top_b:
    if st.button("返回设置", key="back_to_setup"):
        st.session_state.game = None
        st.rerun()

# ============================================================
# 公共牌区（只显示最新一张，低调显示）
# ============================================================
st.divider()

if game.center_pile and game.last_played_card:
    st.caption(
        f"最新记录：{card_to_symbol(game.last_played_card)}　"
        f"（累计 {len(game.center_pile)} 条）"
    )
else:
    st.caption("最新记录：—")

# ============================================================
# 状态区（低调显示）
# ============================================================
me = game.seats[0]

phase_map = {"playing": "进行中", "snapping": "同步中", "finished": "已归档"}

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.caption(f"当前编号：**{game.current_number}**")

with c2:
    if game.phase == "playing":
        st.caption(f"当前操作：**{game.seats[game.current_index].player_id}**")
    elif game.phase == "snapping":
        st.caption("当前操作：**全员确认**")
    else:
        st.caption("当前操作：—")

with c3:
    st.caption(f"阶段：**{phase_map.get(game.phase, game.phase)}**")

with c4:
    st.caption(f"我的剩余：**{len(me.hand)} 张**")

# ============================================================
# 操作按钮（出牌按钮始终渲染，仅置灰）
# ============================================================
st.divider()

if game.phase == "playing":
    if game.current_index == 0:
        st.success("轮到你出牌")
    else:
        st.info(f"等待 {game.seats[game.current_index].player_id} 出牌...")
elif game.phase == "snapping":
    elapsed = time.time() - game.snap_start_time
    remaining = max(0.0, HUMAN_TIMEOUT - elapsed)
    # 抢盖专用提示，加粗黑框
    st.markdown(
        f"<div style='border:2px solid #000; padding:12px; text-align:center; "
        f"font-weight:bold; font-size:16px; background:#fff; color:#000;'>"
        f"编号匹配！全体确认，剩余 {remaining:.1f} 秒（可按空格键）"
        f"</div>",
        unsafe_allow_html=True,
    )
    if me.snap_pressed:
        speed = me.snap_time - game.snap_start_time
        st.caption(f"你已确认（{speed:.2f} 秒）")
else:
    st.caption("")

# ---------- 出牌按钮：位置固定 ----------
play_disabled = not (game.phase == "playing" and game.current_index == 0)
if st.button("出牌", key="play_btn", use_container_width=True, disabled=play_disabled):
    game.play()
    st.rerun()

# ============================================================
# 盖牌按钮 —— 全程可用
# ============================================================
st.markdown("#### 盖牌操作")
st.caption("数字没对上时按下去，会直接拿走中央全部文件。也可以直接按空格键。")

snap_disabled = (not me.hand)
if st.button("盖牌（空格）", key="snap_btn", use_container_width=True,
             disabled=snap_disabled):
    game.press_snap(me)
    st.rerun()

# ============================================================
# 最近结算提示
# ============================================================
if game.last_result:
    if time.time() - game.last_result["timestamp"] < 8:
        if game.last_result.get("reason") == "misfire":
            st.error(
                f"**{game.last_result['loser_id']}** 误确认，"
                f"带走中央 {game.last_result['taken_count']} 张文件"
            )
        else:
            st.warning(
                f"**{game.last_result['loser_id']}** 反应最慢，"
                f"带走中央 {game.last_result['taken_count']} 张文件"
            )

# ============================================================
# 玩家列表
# ============================================================
st.divider()
st.subheader("当前情况")

table_data = []
for i, s in enumerate(game.seats):
    marker = "（我）" if i == 0 else ""
    if game.phase == "snapping" and s.snap_pressed:
        speed = s.snap_time - game.snap_start_time
        status = f"已确认 ({speed:.2f}s)"
    elif game.phase == "playing" and i == game.current_index:
        status = "待出牌"
    elif not s.hand:
        status = "已完成"
    else:
        status = "等待中"

    table_data.append({
        "席位": i + 1,
        "标识": s.player_id + marker,
        "手牌数": len(s.hand),
        "状态": status,
        "最近动作": s.last_action or "—",
    })

st.dataframe(table_data, use_container_width=True, hide_index=True)

# ============================================================
# 结束面板
# ============================================================
if game.phase == "finished":
    st.divider()
    if game.loser_id:
        st.error(f"游戏结束，**{game.loser_id}** 是最终输家。")
        if game.loser_id == game.last_player_id and all(not s.hand for s in game.seats):
            st.caption("（一副牌已全部出完，仍无人匹配，最后出牌者判负）")
    else:
        st.error("游戏结束。")

    if st.button("再来一局", use_container_width=True):
        st.session_state.game = None
        st.rerun()
