import streamlit as st
import random
import string
import time
from dataclasses import dataclass, field
from streamlit_server_state import server_state, server_state_lock
from streamlit_autorefresh import st_autorefresh

# ============================================================
# 页面伪装配置
# ============================================================
st.set_page_config(
    page_title="项目进度同步会",
    layout="wide",
)

hide_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""
st.markdown(hide_style, unsafe_allow_html=True)

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

    code_input = st.text_input(
        "访问凭证",
        type="password",
        key="invite_input",
        placeholder="请输入",
    )

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
ROOM_VERSION = 1
MAX_PLAYERS = 4
SNAP_WINDOW = 3.0  # 抢盖窗口（秒）

SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
RANK_VALUE = {r: i + 1 for i, r in enumerate(RANKS)}  # A=1, 2=2, ..., K=13


def card_to_symbol(card):
    if not card or "-" not in card:
        return card
    rank, suit = card.split("-")
    return f"{rank}{SUIT_SYMBOL.get(suit, suit)}"


def cards_to_html(cards, gap="  "):
    inner = gap.join(card_to_symbol(c) for c in cards)
    return (
        f"<span style='color: black !important; "
        f"font-family: Consolas, Menlo, monospace; "
        f"font-size: 22px;'>{inner}</span>"
    )


def card_rank_value(card):
    if not card or "-" not in card:
        return 0
    return RANK_VALUE.get(card.split("-")[0], 0)


# ============================================================
# 玩家与房间
# ============================================================
@dataclass
class SnapPlayer:
    player_id: str
    hand: list = field(default_factory=list)
    last_heartbeat: float = 0.0
    snap_time: float = 0.0
    snap_pressed: bool = False


class SnapRoom:
    def __init__(self, max_players=MAX_PLAYERS):
        self.version = ROOM_VERSION
        self.max_players = max_players
        self.seats: list = [None] * max_players
        self.center_pile: list = []
        self.current_index = 0
        self.current_number = 1
        self.phase = "waiting"  # waiting / playing / snapping / finished
        self.snap_start_time = 0.0
        self.last_result = None
        self.loser_id = None
        self.game_active = False

    # ---------- 基础 ----------
    def player_count(self):
        return sum(1 for s in self.seats if s is not None)

    def is_full(self):
        return self.player_count() >= self.max_players

    def has_player(self, pid):
        return any(s is not None and s.player_id == pid for s in self.seats)

    def is_seat_empty(self, seat_index):
        if seat_index < 0 or seat_index >= self.max_players:
            return False
        return self.seats[seat_index] is None

    def add_player_at(self, pid, seat_index):
        if seat_index < 0 or seat_index >= self.max_players:
            return False
        if self.seats[seat_index] is not None:
            return False
        p = SnapPlayer(player_id=pid)
        p.last_heartbeat = time.time()
        self.seats[seat_index] = p
        return True

    def remove_player(self, pid):
        for i in range(self.max_players):
            if self.seats[i] is not None and self.seats[i].player_id == pid:
                self.seats[i] = None
                return

    def get_player(self, pid):
        for s in self.seats:
            if s is not None and s.player_id == pid:
                return s
        return None

    def heartbeat(self, pid):
        p = self.get_player(pid)
        if p:
            p.last_heartbeat = time.time()

    def cleanup_stale(self, timeout=25):
        now = time.time()
        for i in range(self.max_players):
            s = self.seats[i]
            if s is None:
                continue
            if (now - s.last_heartbeat) > timeout:
                self.seats[i] = None

    def _next_active_index(self, start):
        for offset in range(1, self.max_players + 1):
            idx = (start + offset) % self.max_players
            s = self.seats[idx]
            if s is not None and len(s.hand) > 0:
                return idx
        return -1

    # ---------- 游戏流程 ----------
    def start_game(self):
        players = [s for s in self.seats if s is not None]
        if len(players) < 2:
            return False

        deck = [f"{r}-{s}" for r in RANKS for s in SUITS]
        random.shuffle(deck)

        n = len(players)
        for p in players:
            p.hand = []
        for i, card in enumerate(deck):
            players[i % n].hand.append(card)

        self.center_pile = []
        self.current_number = 1
        self.current_index = 0
        guard = 0
        while (self.seats[self.current_index] is None
               or len(self.seats[self.current_index].hand) == 0) and guard < 10:
            self.current_index = (self.current_index + 1) % self.max_players
            guard += 1

        for p in players:
            p.snap_time = 0.0
            p.snap_pressed = False

        self.phase = "playing"
        self.game_active = True
        self.last_result = None
        self.loser_id = None
        return True

    def play_card(self, pid):
        if self.phase != "playing":
            return False
        if self.current_index < 0 or self.current_index >= self.max_players:
            return False
        seat = self.seats[self.current_index]
        if seat is None or seat.player_id != pid:
            return False
        if len(seat.hand) == 0:
            return False

        card = seat.hand.pop(0)
        self.center_pile.append(card)
        rank_val = card_rank_value(card)
        spoken_number = self.current_number

        # 匹配 → 进入抢盖阶段
        if rank_val == spoken_number:
            self.phase = "snapping"
            self.snap_start_time = time.time()
            for s in self.seats:
                if s is not None:
                    s.snap_time = 0.0
                    s.snap_pressed = False
            return True

        # 不匹配 → 数字递增，轮到下家
        self.current_number = self.current_number % 13 + 1

        players_with_cards = [s for s in self.seats if s is not None and len(s.hand) > 0]
        if len(players_with_cards) <= 1:
            self.phase = "finished"
            self.game_active = False
            if players_with_cards:
                self.loser_id = players_with_cards[0].player_id
            return True

        next_idx = self._next_active_index(self.current_index)
        if next_idx < 0:
            self.phase = "finished"
            self.game_active = False
            return True
        self.current_index = next_idx
        return True

    def snap(self, pid):
        if self.phase != "snapping":
            return False
        p = self.get_player(pid)
        if p is None or p.snap_pressed:
            return False
        if len(p.hand) == 0:
            return False
        p.snap_pressed = True
        p.snap_time = time.time()
        return True

    def resolve_snap(self):
        if self.phase != "snapping":
            return

        seated = [s for s in self.seats if s is not None]
        participants = [s for s in seated if len(s.hand) > 0]

        if not participants:
            self.phase = "playing"
            return

        # 找出最慢的
        slowest = None
        slowest_time = -1.0
        for s in participants:
            if not s.snap_pressed:
                slowest = s
                slowest_time = float("inf")
                break
            if s.snap_time > slowest_time:
                slowest_time = s.snap_time
                slowest = s

        if slowest is None:
            slowest = participants[0]

        taken = self.center_pile[:]
        slowest.hand.extend(taken)
        self.center_pile = []

        self.loser_id = slowest.player_id
        self.last_result = {
            "loser_id": slowest.player_id,
            "taken_count": len(taken),
            "taken_cards": taken,
            "timestamp": time.time(),
        }

        for s in seated:
            s.snap_time = 0.0
            s.snap_pressed = False

        # 游戏结束检查：只剩一人有牌
        players_with_cards = [s for s in self.seats if s is not None and len(s.hand) > 0]
        if len(players_with_cards) <= 1:
            self.phase = "finished"
            self.game_active = False
            if players_with_cards:
                self.loser_id = players_with_cards[0].player_id
            return

        # 继续下一轮：从输家下家开始
        loser_idx = -1
        for i, s in enumerate(self.seats):
            if s is not None and s.player_id == slowest.player_id:
                loser_idx = i
                break

        self.current_number = 1
        self.phase = "playing"

        if loser_idx >= 0:
            next_idx = self._next_active_index(loser_idx)
            if next_idx < 0:
                next_idx = loser_idx
            self.current_index = next_idx


# ============================================================
# 玩家 ID
# ============================================================
if "player_id" not in st.session_state:
    if "pid" in st.query_params:
        st.session_state.player_id = st.query_params["pid"]
    else:
        new_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        st.session_state.player_id = new_id
        st.query_params["pid"] = new_id

my_id = st.session_state.player_id

# ============================================================
# 自动刷新
# ============================================================
st_autorefresh(interval=700, key="auto_refresh")

# ============================================================
# 房间初始化 + 抢盖窗口结算
# ============================================================
with server_state_lock["snap_room"]:
    need_new = False
    if "snap_room" not in server_state:
        need_new = True
    elif getattr(server_state.snap_room, "version", 0) != ROOM_VERSION:
        need_new = True
    elif not hasattr(server_state.snap_room, "last_result"):
        need_new = True

    if need_new:
        server_state.snap_room = SnapRoom(max_players=MAX_PLAYERS)

    room = server_state.snap_room
    room.heartbeat(my_id)
    room.cleanup_stale(timeout=25)

    # 抢盖窗口自动结算
    if room.phase == "snapping":
        elapsed = time.time() - room.snap_start_time
        participants = [s for s in room.seats if s is not None and len(s.hand) > 0]
        all_snapped = len(participants) > 0 and all(s.snap_pressed for s in participants)
        if elapsed >= SNAP_WINDOW or all_snapped:
            room.resolve_snap()
            server_state.snap_room = room

# ============================================================
# 大厅（未入座）
# ============================================================
if not room.has_player(my_id):
    st.subheader("请选择你要进入的项目组")

    if room.is_full():
        st.warning("当前项目组已满，请稍后再试。")
        st.stop()

    if st.session_state.get("pending_seat") is not None:
        pending_i = st.session_state.pending_seat
        st.info(f"你选择了 **项目组{pending_i+1}**，请输入你的标识名称")

        name_input = st.text_input(
            "标识名称（最多 12 个字）",
            value="",
            max_chars=12,
            key="join_name_input",
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("确认进入", key="confirm_join", use_container_width=True):
                name_clean = name_input.strip()
                if not name_clean:
                    st.error("标识名称不能为空")
                elif room.has_player(name_clean):
                    st.error(f"标识名称「{name_clean}」已被占用，请换一个")
                elif not room.is_seat_empty(pending_i):
                    st.error(f"项目组{pending_i+1}已被占用，请重新选择")
                    st.session_state.pending_seat = None
                    st.rerun()
                else:
                    st.session_state.player_id = name_clean
                    st.session_state.pending_seat = None

                    with server_state_lock["snap_room"]:
                        room.add_player_at(name_clean, pending_i)
                        server_state.snap_room = room

                    st.rerun()
        with c2:
            if st.button("取消", key="cancel_join", use_container_width=True):
                st.session_state.pending_seat = None
                st.rerun()

        st.stop()

    cols = st.columns(4)
    for i in range(MAX_PLAYERS):
        with cols[i % 4]:
            if room.is_seat_empty(i):
                if st.button(f"项目组{i+1}", key=f"seat_{i}"):
                    st.session_state.pending_seat = i
                    st.rerun()
            else:
                seat = room.seats[i]
                st.button(
                    f"项目组{i+1}（{seat.player_id}）",
                    disabled=True,
                    key=f"seat_{i}",
                )

    st.stop()

st.session_state.pending_seat = None

# ============================================================
# 顶部栏
# ============================================================
top_col_a, top_col_b = st.columns([4, 1])
with top_col_a:
    st.caption("多人协作模式")
with top_col_b:
    if st.button("← 返回大厅", key="back_from_seat"):
        with server_state_lock["snap_room"]:
            me_leave = room.get_player(my_id)
            if me_leave:
                # 把牌放回中央
                room.center_pile.extend(me_leave.hand)
                me_leave.hand = []
            room.remove_player(my_id)
            server_state.snap_room = room
        st.rerun()

me = room.get_player(my_id)

# ============================================================
# 中央公共牌区
# ============================================================
st.divider()
st.subheader("待处理文件堆（公共牌区）")

if room.center_pile:
    display_cards = room.center_pile[-12:]
    st.markdown(
        f"<div style='text-align:center; padding:18px; background:#f0f2f6; "
        f"border-radius:10px; min-height:60px;'>"
        f"{cards_to_html(display_cards)}"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"堆中共 {len(room.center_pile)} 张文件")
else:
    st.markdown(
        "<div style='text-align:center; padding:18px; color:gray; "
        "background:#f0f2f6; border-radius:10px;'>"
        "暂无文件"
        "</div>",
        unsafe_allow_html=True,
    )

# ============================================================
# 当前状态
# ============================================================
st.divider()

status_col1, status_col2, status_col3 = st.columns(3)
with status_col1:
    st.metric("当前进度编号", room.current_number)
with status_col2:
    if room.phase == "playing" and 0 <= room.current_index < MAX_PLAYERS:
        seat = room.seats[room.current_index]
        st.metric("当前操作人", seat.player_id if seat else "—")
    elif room.phase == "snapping":
        st.metric("当前操作人", "⚡ 全体抢盖！")
    elif room.phase == "finished":
        st.metric("当前操作人", "游戏结束")
    else:
        st.metric("当前操作人", "—")
with status_col3:
    phase_map = {
        "waiting": "等待中",
        "playing": "进行中",
        "snapping": "⚡ 抢盖阶段",
        "finished": "已结束",
    }
    st.metric("阶段", phase_map.get(room.phase, room.phase))

# ============================================================
# 操作按钮
# ============================================================
st.divider()

# 出牌按钮
if room.phase == "playing" and me and 0 <= room.current_index < MAX_PLAYERS:
    current_seat = room.seats[room.current_index]
    if current_seat and current_seat.player_id == my_id:
        st.success("轮到你出牌")
        if st.button("📤 出牌", key="play_btn", use_container_width=True, type="primary"):
            with server_state_lock["snap_room"]:
                room.play_card(my_id)
                server_state.snap_room = room
            st.rerun()
    else:
        who = current_seat.player_id if current_seat else "其他玩家"
        st.info(f"等待 {who} 出牌...")

# 盖牌按钮
if room.phase == "snapping":
    elapsed = time.time() - room.snap_start_time
    remaining = max(0, SNAP_WINDOW - elapsed)
    st.error(f"⚡ 编号匹配！全体抢盖！剩余 {remaining:.1f} 秒")

    if me and me.snap_pressed:
        my_speed = me.snap_time - room.snap_start_time
        st.success(f"你已盖牌 ✓ （{my_speed:.2f} 秒）")
    else:
        if st.button("✋ 盖牌！", key="snap_btn", use_container_width=True, type="primary"):
            with server_state_lock["snap_room"]:
                room.snap(my_id)
                server_state.snap_room = room
            st.rerun()

# ============================================================
# 结算显示
# ============================================================
if room.last_result and room.phase in ("playing", "snapping", "finished"):
    result = room.last_result
    if time.time() - result.get("timestamp", 0) < 10:
        st.warning(
            f"⚠️ **{result['loser_id']}** 反应最慢，带走中央 "
            f"{result['taken_count']} 张文件"
        )
        if result.get("taken_cards"):
            with st.expander("查看带走的文件"):
                st.markdown(
                    cards_to_html(result["taken_cards"]),
                    unsafe_allow_html=True,
                )

# ============================================================
# 玩家列表
# ============================================================
st.divider()
st.subheader("当前情况")

table_data = []
for i in range(MAX_PLAYERS):
    seat = room.seats[i]
    if seat is None:
        table_data.append({
            "席位": i + 1,
            "标识": "(空位)",
            "手牌数": "—",
            "状态": "—",
        })
    else:
        is_me = " ◀" if seat.player_id == my_id else ""
        if room.phase == "snapping" and seat.snap_pressed:
            speed = seat.snap_time - room.snap_start_time
            status = f"已盖牌 ({speed:.2f}s)"
        elif room.phase == "playing" and i == room.current_index:
            status = "待出牌"
        elif len(seat.hand) == 0:
            status = "已完成 ✓"
        else:
            status = "等待中"

        table_data.append({
            "席位": i + 1,
            "标识": seat.player_id + is_me,
            "手牌数": len(seat.hand),
            "状态": status,
        })

st.dataframe(table_data, use_container_width=True, hide_index=True)

# ============================================================
# 底部控制
# ============================================================
st.divider()

if room.phase == "waiting":
    if room.player_count() >= 2:
        if st.button("🎮 开始游戏", key="start_game", use_container_width=True, type="primary"):
            with server_state_lock["snap_room"]:
                room.start_game()
                server_state.snap_room = room
            st.rerun()
    else:
        st.info(f"等待玩家加入...（当前 {room.player_count()}/4，至少 2 人）")

elif room.phase == "finished":
    if room.loser_id:
        st.error(f"🏁 游戏结束！**{room.loser_id}** 拿走了所有文件，是最终输家！")
    if st.button("🔄 开始新一局", key="restart", use_container_width=True, type="primary"):
        with server_state_lock["snap_room"]:
            room.start_game()
            server_state.snap_room = room
        st.rerun()
