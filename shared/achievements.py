"""Achievement metadata — pure data, no discord/casino deps, so both the bot
(bot/cogs/progression.py) and the web layer (web/hq.py) can import it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Achievement:
    id: str
    name: str
    description: str
    category: str  # "Progression", "Winning", "Diversity", "Social", "Daily", "Wealth", "Betting", "Investing", "Web"
    emoji: str
    xp_reward: int


ALL_ACHIEVEMENTS: list[Achievement] = [
    # ── Progression ──
    Achievement("first_game", "First Steps", "Play your first casino game", "Progression", "\U0001f3ae", 10),
    Achievement("play_10", "Regular", "Play 10 casino games", "Progression", "\U0001f3af", 25),
    Achievement("play_100", "Grinder", "Play 100 casino games", "Progression", "⚙️", 100),
    Achievement("play_500", "Veteran", "Play 500 casino games", "Progression", "\U0001f396️", 250),
    # ── Winning ──
    Achievement("first_win", "Winner", "Win your first casino game", "Winning", "\U0001f3c6", 10),
    Achievement("streak_3", "Hot Streak", "Win 3 games in a row", "Winning", "\U0001f525", 50),
    Achievement("streak_5", "On Fire", "Win 5 games in a row", "Winning", "\U0001f525", 100),
    Achievement("streak_10", "Unstoppable", "Win 10 games in a row", "Winning", "\U0001f4a5", 250),
    Achievement("big_win", "Big Winner", "Profit 1000+ coins in one game", "Winning", "\U0001f4b0", 50),
    Achievement("jackpot", "Jackpot", "Profit 5000+ coins in one game", "Winning", "\U0001f48e", 150),
    # ── Diversity ──
    Achievement("explore_5", "Explorer", "Play 5 different games", "Diversity", "\U0001f5fa️", 25),
    Achievement("explore_15", "World Traveler", "Play 15 different games", "Diversity", "\U0001f30d", 75),
    Achievement("explore_all", "Completionist", "Play every casino game at least once", "Diversity", "⭐", 200),
    # ── Social ──
    Achievement("duel_win", "Challenger", "Win a duel", "Social", "⚔️", 25),
    Achievement("duel_10", "Duelist", "Win 10 duels", "Social", "\U0001f5e1️", 100),
    Achievement("tourney_win", "Tournament Victor", "Win a tournament", "Social", "\U0001f3c5", 150),
    Achievement("tourney_5", "Champion", "Win 5 tournaments", "Social", "\U0001f451", 500),
    # ── Daily ──
    Achievement("daily_1", "Task Master", "Complete a daily challenge", "Daily", "\U0001f4cb", 15),
    Achievement("daily_all", "Dedicated", "Complete all 3 daily challenges in one day", "Daily", "\U0001f4cb", 50),
    Achievement("daily_7", "Devoted", "Complete all daily challenges 7 days in a row", "Daily", "\U0001f5d3️", 200),
    # ── Wealth ──
    Achievement("coins_5k", "Thousandaire", "Reach 5,000 coin balance", "Wealth", "\U0001f4b5", 25),
    Achievement("coins_25k", "Wealthy", "Reach 25,000 coin balance", "Wealth", "\U0001f4b0", 75),
    Achievement("coins_100k", "Mogul", "Reach 100,000 coin balance", "Wealth", "\U0001f911", 200),
    Achievement("wager_50k", "High Roller", "Wager 50,000+ total coins", "Wealth", "\U0001f3b2", 100),
    Achievement("wager_500k", "Whale", "Wager 500,000+ total coins", "Wealth", "\U0001f40b", 300),
    # ── Betting (sportsbook: /bet, CLV) ──
    Achievement("bet_first", "On the Board", "Log your first bet", "Betting", "\U0001f4dd", 15),
    Achievement("bet_10", "Regular Bettor", "Log 10 bets", "Betting", "\U0001f3ab", 40),
    Achievement("bet_50", "Bookie's Nightmare", "Log 50 bets", "Betting", "\U0001f4da", 150),
    Achievement("bet_win", "Cashed Ticket", "Win a bet", "Betting", "\U0001f4b8", 20),
    Achievement("clv_beat", "Beat the Close", "Log a bet with positive CLV", "Betting", "\U0001f4c8", 30),
    Achievement("clv_10", "Sharp Money", "Beat the closing line on 10 bets", "Betting", "\U0001f52a", 120),
    # ── Investing (stocks / crypto / options) ──
    Achievement("stock_first", "First Position", "Record your first stock trade", "Investing", "\U0001f4c8", 15),
    Achievement("stock_10", "Active Trader", "Make 10 stock trades", "Investing", "\U0001f4b9", 50),
    Achievement("stock_diversified", "Diversified", "Hold 10 different positions at once", "Investing", "\U0001f9fa", 75),
    Achievement("stock_green", "In the Green", "Reach $1,000 realized stock profit", "Investing", "\U0001f7e2", 100),
    Achievement("stock_bull", "Bull Market", "Reach $10,000 realized stock profit", "Investing", "\U0001f402", 300),
    Achievement("crypto_first", "Crypto Curious", "Buy your first crypto", "Investing", "\U0001fa99", 25),
    Achievement("options_first", "Optioned Up", "Trade your first option contract", "Investing", "\U0001f4d1", 25),
    Achievement("stock_50", "Day Trader", "Make 50 stock trades", "Investing", "\U0001f4ca", 150),
    Achievement("options_5", "Contract Killer", "Make 5 option trades", "Investing", "\U0001f9fe", 75),
    Achievement("bet_100", "Wiseguy", "Log 100 bets", "Betting", "\U0001f3b0", 300),
    # ── Web / HQ ──
    Achievement("web_login", "Plugged In", "Sign in to SharpLab HQ", "Web", "\U0001f50c", 20),
    Achievement("web_trade", "Browser Trader", "Log a trade from the website", "Web", "\U0001f5b1️", 30),
    Achievement("web_regular", "HQ Regular", "Rack up 25 visits to SharpLab HQ", "Web", "\U0001f310", 75),
    # ── Progression (level milestones) ──
    Achievement("level_10", "Seasoned", "Reach level 10", "Progression", "\U0001f396️", 150),
    Achievement("level_25", "Elite", "Reach level 25", "Progression", "\U0001f451", 500),
    Achievement("level_50", "Legend", "Reach level 50", "Progression", "\U0001f3c5", 1000),
    # ── Voice ──
    Achievement("voice_1h", "Mic Check", "Spend 1 hour in voice", "Voice", "\U0001f399️", 40),
    Achievement("voice_10h", "Voice Regular", "Spend 10 hours in voice", "Voice", "\U0001f3a7", 120),
    Achievement("voice_50h", "Always On", "Spend 50 hours in voice", "Voice", "\U0001f4e1", 350),
    # ── Chat ──
    Achievement("chat_100", "Chatterbox", "Send 100 messages", "Chat", "\U0001f4ac", 25),
    Achievement("chat_1k", "Loudmouth", "Send 1,000 messages", "Chat", "\U0001f5e3️", 100),
    Achievement("chat_10k", "Yapper", "Send 10,000 messages", "Chat", "\U0001f4e3", 300),
]

ACHIEVEMENTS_BY_ID: dict[str, Achievement] = {a.id: a for a in ALL_ACHIEVEMENTS}

CATEGORIES = ["Progression", "Winning", "Diversity", "Social", "Daily",
              "Wealth", "Betting", "Investing", "Web", "Voice", "Chat"]
