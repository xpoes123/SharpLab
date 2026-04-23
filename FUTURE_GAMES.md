# Future Games — Build Later

Ideas for math/logic/quant-heavy games to add to the casino.

## 1. Figgie (Jane Street trading game)
4 suits of cards, one is the "goal suit" worth 10/card at game end. Players openly bid/offer cards to each other trying to deduce which suit is valuable and accumulate it. Pure information extraction + trading speed. The canonical quant intern game.

## 2. Market Making
A mystery asset has a true value revealed at the end. Each round, players post bid/ask quotes. An NPC "informed trader" sometimes hits your quote. Tight spreads = more flow but more adverse selection risk. Widest P&L wins. Teaches market microstructure.

## 3. Options Pricing
Flash a scenario (underlying price, vol, time to expiry, strike) and players estimate the fair option value. Closest to Black-Scholes wins. Variants: "which of these 3 options is mispriced?", or Greeks estimation.

## 4. Colonel Blotto
Allocate N soldiers across K battlefields (hidden, simultaneous). Whoever has more on a battlefield wins it. Most battlefields won = overall winner. Simple rules, absurdly deep mixed-strategy Nash equilibria.

## 5. Prisoner's Dilemma Tournament
Iterated PD, ~20 rounds. Players submit a strategy (cooperate/defect each round, can condition on opponent's history). Tit-for-tat, grim trigger, etc. Round-robin tournament, highest cumulative payoff wins.

## 6. Sequence
Show 4-6 terms of a mathematical sequence, guess the next term. Mix OEIS-style integer sequences with pattern recognition. Harder rounds give fewer terms. Speed + math intuition.

## 7. Mental Math Sprint
10 rapid-fire problems per round (3-digit multiplication, quick combinatorics, percentage estimation, etc). Fastest correct answer wins each problem. Can run as tournament bracket.

## 8. Connect 4
7 columns, drop pieces, first to connect 4 in a row/column/diagonal wins. Render board in monospace. 1v1 — perfect for duels. Coordinate input via buttons (column select). Surprisingly strategic despite simple rules.

## 9. Battleship
Hidden board, place ships, take turns firing. Probability-driven search — optimal play is entropy-maximizing. Render two boards (yours + tracking). 1v1 only. Great test of systematic search vs. intuition.

## 10. Bayesian Detective
A crime happened. 5 suspects, each with a prior probability. Each round reveals a clue (evidence with known likelihood ratios) and players update their posterior distribution over suspects. Submit your probability distribution. Scored by Brier score / log loss — perfect calibration wins. Literally trains Bayesian reasoning.

## 11. Coup
Bluffing card game. Each player has 2 influence cards (hidden). On your turn, claim any role's action (Duke: tax, Assassin: assassinate, Captain: steal, etc). Anyone can challenge your claim — if you're bluffing, you lose a card; if you're telling the truth, the challenger loses one. Last player with influence wins. Mixed-strategy equilibrium game — bluff frequency is the skill expression.

## 12. Indian Poker (Sit & Go)
Each player gets one card face-up on their "forehead" — everyone can see everyone else's card but not their own. Betting rounds like poker: check, bet, raise, fold. You infer your card's strength from how others bet. Sit-and-go format: fixed buy-in, play until one player has all the chips. Classic information asymmetry game.

## 13. March Madness Bracket
8-team single-elimination bracket using real NCAA basketball teams. Each game draws from a curated pool of top programs (Duke, UConn, Kansas, Gonzaga, Kentucky, etc.) and assigns three ratings: **Offense** (scoring ability), **Defense** (stops), and **Coaching** (adjustments/clutch factor). Ratings are randomized within a plausible range each tournament so no bracket is the same twice.

**Simulation:** Matchups resolved probabilistically — offense vs. opposing defense produces an expected scoring differential, coaching acts as a multiplier in close games. Higher composite overall = higher win probability, but upsets happen (weighted randomness, not deterministic).

**Solo play:** Pick all 7 winners across 3 rounds (quarters → semis → final). Score = correct picks, weighted by round (1 pt first round, 2 pts semis, 4 pts final). Leaderboard tracks best scores.

**Multiplayer:** All players fill out brackets simultaneously (hidden until lock). After lock, the sim runs live round-by-round with reveals. Payout ladder: last place gets nothing, scales up to winner. Ties broken by closest-to-actual total points in the final.

**Key design notes:**
- Show team ratings before bracket lock so picks are informed, not random
- Render bracket as monospace ASCII or embed image
- Each round reveal is a separate message for suspense
- Side pots: optional "upset special" bonus for correctly calling a lower-seed win

## 14. Lyric Guesser
Play a snippet of song lyrics (3-4 lines, censoring the title/artist name if it appears). Players guess the song title and/or artist. Faster correct answer = more points. Rounds escalate: first hint is a deep-cut line, second is the chorus, third is a dead giveaway. Can theme rounds (90s hip-hop, pop hits, etc.). Source lyrics from a curated bank to avoid API/copyright issues. Solo or multiplayer — works great as a timed sprint.

## 15. NBA Silhouette Guesser
Show a blacked-out silhouette of an NBA player (action shot — dunk, jumper, celebration) and players guess who it is. Progressive hints: silhouette only → add jersey number → add team colors → reveal partial face. Fewer hints needed = more points. Source from a curated image bank of ~100+ recognizable players. Variant: guess the era (70s/80s/90s/00s/10s/20s) from the silhouette style. Great sports knowledge test.

## 16. Stat Line Guesser
Show a stat line from a real NBA game (e.g., "38 PTS / 7 REB / 6 AST / 4 STL") and players guess which player posted it. Harder rounds use role players or historic lines. Variants: guess the game/opponent, guess if the team won or lost, or "higher or lower" (did this player average more or fewer PPG that season?). Deep basketball knowledge expression.

## 17. Over/Under Trivia
Flash a question with a numeric answer (e.g., "How many career points does LeBron have?" or "What's the population of Tokyo?"). Set a line. Players bet over or under. Reveal the answer — correct side wins. Basically prop betting on trivia. Can theme rounds (sports stats, geography, science, history). Plays into the betting DNA of the server.

## 18. Who Said It?
Show a quote and 3-4 possible speakers (athletes, coaches, public figures). Players pick who said it. Rounds mix famous quotes with obscure press conference gems. Variant: "player or AI?" — was this post-game quote real or generated? Tests cultural literacy + BS detection.

## 19. Emoji Movie / Song
Translate a movie or song title into 3-5 emojis. Players race to decode it. Simple, fast, zero-infrastructure — just a curated bank of emoji puzzles. Works great as a duel mini-game or tournament filler round.

## 20. Price Is Right (Sneakers / Cards Edition)
Show a sneaker, trading card, or sports memorabilia item. Players guess the resale price. Closest without going over wins (classic Price Is Right rules). Source from a curated bank with known market values. Teaches market intuition in a fun wrapper.
