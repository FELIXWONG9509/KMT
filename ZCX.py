import streamlit as st
import random
import time
from dataclasses import dataclass, field
from streamlit_autorefresh import st_autorefresh

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
SNAP_WINDOW = 3.0          # 正确抢盖窗口（秒）
AI_MISFIRE_RATE = 0.004    # 每个 AI 每次刷新误盖概率
REFRESH_MS = 500

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
RANK_VALUE = {r: i + 1 for i, r in enumerate(RANKS)}
SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def card_to_symbol(card):
    rank, suit = card.split("-")
    return f"{rank}{SUIT_SYMBOL[suit]}"


def cards_to_html(cards, gap="  "):
    inner = gap.join(card_to_symbol(c) for c in cards)
    return (
        f"<span style='color:black;font-family:Consolas,Menlo,monospace;"
        f"font-size:22px;'>{inner}</span>"
    )


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
        self.phase = "playing"   # playing / snapping / finished
        self.snap_start_time = 0.0
        self.last_result = None
        self.loser_id = None
        self.next_ai_play_time = 0.0
        self.last_played_card = None
        self.last_played_number = 0

    # ---------- 开局 ----------
    def start(self):
        deck = [f"{r}-{s}" for r in RANKS for s in SUITS]
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
        self.last_result = None
        self.loser_id = None
        self.last_played_card = None
        self.last_played_number = 0
        self._set_ai_timer()

    # ---------- 工具 ----------
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
        if len(with_cards) <= 1:
            self.phase = "finished"
            self.loser_id = with_cards[0].player_id if with_cards else None
            return True
        return False

    # ---------- 正常出牌 ----------
    def play(self):
        seat = self.seats[self.current_index]
        if not seat.hand:
            return

        card = seat.hand.pop(0)
        self.center_pile.append(card)
        rank_val = RANK_VALUE[card.split("-")[0]]

        self.last_played_card = card
        self.last_played_number = self.current_number
        seat.last_action = f"提交 {card_to_symbol(card)}（编号 {self.current_number}）"

        # 匹配 → 进入抢盖阶段
        if rank_val == self.current_number:
            self._start_snapping()
            return

        # 不匹配 → 编号+1，轮到下家
        self.current_number = self.current_number % 13 + 1

        if self._check_game_over():
            return

        nxt = self._next_player_with_cards(self.current_index)
        if nxt < 0:
            self.phase = "finished"
            return
        self.current_index = nxt
        self._set_ai_timer()

    # ---------- 抢盖 ----------
    def _start_snapping(self):
        self.phase = "snapping"
        self.snap_start_time = time.time()
        for s in self.seats:
            s.snap_pressed = False
            s.snap_time = 0.0
            if s.is_ai and s.hand:
                # AI 反应时间：0.4 ~ 2.7 秒
                s.ai_snap_delay = random.uniform(0.4, SNAP_WINDOW - 0.3)
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
                s.last_action = "已盖牌"

    # ---------- 盖牌（全程可用） ----------
    def press_snap(self, player):
        """
        任何时刻都能按盖牌：
        - 抢盖阶段按 → 正常计时
        - 平时按 → 误盖，直接拿走中央全部牌
        """
        if player is None or not player.hand:
            return

        if self.phase == "snapping":
            if player.snap_pressed:
                return
            player.snap_pressed = True
            player.snap_time = time.time()
            player.last_action = "已盖牌"
            return

        if self.phase == "playing":
            self._misfire(player)

    def _misfire(self, player):
        """误盖惩罚：拿走中央所有牌。"""
        if not self.center_pile:
            player.last_action = "误盖（中央为空）"
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
        player.last_action = f"误盖，带走 {len(taken)} 张"

        if self._check_game_over():
            return

        # 不改变当前回合，游戏继续
        # 若当前玩家已没牌，跳到下家
        if not self.seats[self.current_index].hand:
            nxt = self._next_player_with_cards(self.current_index)
            if nxt < 0:
                self.phase = "finished"
                return
            self.current_index = nxt
        self._set_ai_timer()

    def update_ai_misfire(self):
        """AI 偶尔会手痒误盖。"""
        if self.phase != "playing":
            return
        for s in self.seats:
            if not s.is_ai or not s.hand:
                continue
            if not self.center_pile:
                continue
            if random.random() < AI_MISFIRE_RATE:
                self._misfire(s)
                return

    # ---------- 结算抢盖 ----------
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
    st.caption("盖牌按钮全程可用——对上了再按，按错要罚。")

    if st.button("开始游戏", type="primary", use_container_width=True):
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

# 抢盖窗口结算
if game.phase == "snapping":
    elapsed = time.time() - game.snap_start_time
    participants = [s for s in game.seats if s.hand]
    all_snapped = len(participants) > 0 and all(s.snap_pressed for s in participants)
    if elapsed >= SNAP_WINDOW or all_snapped:
        game.resolve_snapping()
        st.rerun()

# AI 自动出牌
if game.phase == "playing":
    seat = game.seats[game.current_index]
    if seat.is_ai and time.time() >= game.next_ai_play_time:
        game.play()
        st.rerun()

# ============================================================
# 顶部信息
# ============================================================
top_a, top_b = st.columns([4, 1])
with top_a:
    st.caption("单人模拟模式（对方为 AI 账户）")
with top_b:
    if st.button("← 返回设置", key="back_to_setup"):
        st.session_state.game = None
        st.rerun()

# ============================================================
# 公共牌区
# ============================================================
st.divider()
st.subheader("待处理文件堆（公共牌区）")

if game.center_pile:
    recent = game.center_pile[-15:]
    st.markdown(
        "<div style='text-align:center;padding:22px;background:#f0f2f6;"
        "border-radius:10px;'>"
        f"{cards_to_html(recent)}</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"堆中共 {len(game.center_pile)} 张文件")
else:
    st.markdown(
        "<div style='text-align:center;padding:22px;color:gray;"
        "background:#f0f2f6;border-radius:10px;'>暂无文件</div>",
        unsafe_allow_html=True,
    )

# ============================================================
# 状态区
# ============================================================
st.divider()
c1, c2, c3 = st.columns(3)

with c1:
    st.metric("当前编号", game.current_number)

with c2:
    if game.phase == "playing":
        st.metric("当前操作人", game.seats[game.current_index].player_id)
    elif game.phase == "snapping":
        st.metric("当前操作人", "⚡ 全体抢盖")
    else:
        st.metric("当前操作人", "—")

with c3:
    phase_map = {"playing": "进行中", "snapping": "抢盖", "finished": "已结束"}
    st.metric("阶段", phase_map.get(game.phase, game.phase))

# ============================================================
# 操作按钮
# ============================================================
st.divider()

me = game.seats[0]

# 出牌按钮（只在轮到你时出现）
if game.phase == "playing":
    if game.current_index == 0:
        st.success("轮到你出牌")
        if st.button("📤 出牌", key="play_btn", use_container_width=True, type="primary"):
            game.play()
            st.rerun()
    else:
        st.info(f"等待 {game.seats[game.current_index].player_id} 出牌...")

# 抢盖倒计时提示
if game.phase == "snapping":
    elapsed = time.time() - game.snap_start_time
    remaining = max(0.0, SNAP_WINDOW - elapsed)
    st.error(f"⚡ 编号匹配！全体抢盖！剩余 {remaining:.1f} 秒")
    if me.snap_pressed:
        speed = me.snap_time - game.snap_start_time
        st.success(f"你已盖牌 ✓（{speed:.2f} 秒）")

# ============================================================
# 盖牌按钮 —— 全程可用
# ============================================================
st.markdown("#### 盖牌操作")
st.caption("⚠️ 注意：数字没对上时按下去，会直接拿走中央全部文件。")

snap_disabled = (not me.hand)
snap_label = "✋ 盖牌！"
if st.button(snap_label, key="snap_btn", use_container_width=True,
             type="primary", disabled=snap_disabled):
    game.press_snap(me)
    st.rerun()

# ============================================================
# 最近结算提示
# ============================================================
if game.last_result:
    if time.time() - game.last_result["timestamp"] < 8:
        if game.last_result.get("reason") == "misfire":
            st.error(
                f"❌ **{game.last_result['loser_id']}** 误盖！"
                f"带走中央 {game.last_result['taken_count']} 张文件"
            )
        else:
            st.warning(
                f"⚠️ **{game.last_result['loser_id']}** 反应最慢，"
                f"带走中央 {game.last_result['taken_count']} 张文件"
            )

# ============================================================
# 玩家列表
# ============================================================
st.divider()
st.subheader("当前情况")

table_data = []
for i, s in enumerate(game.seats):
    marker = " ◀" if i == 0 else ""
    if game.phase == "snapping" and s.snap_pressed:
        speed = s.snap_time - game.snap_start_time
        status = f"已盖牌 ({speed:.2f}s)"
    elif game.phase == "playing" and i == game.current_index:
        status = "待出牌"
    elif not s.hand:
        status = "已完成 ✓"
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
        st.error(f"🏁 游戏结束！**{game.loser_id}** 手里最后还有牌，是最终输家！")
    else:
        st.error("🏁 游戏结束！")

    if st.button("🔄 再来一局", use_container_width=True, type="primary"):
        st.session_state.game = None
        st.rerun()
