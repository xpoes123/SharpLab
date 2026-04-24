# Future Games — Build Later

Ideas for math/logic/quant-heavy games to add to the casino.

## 1. Figgie (Jane Street trading game)
4 suits of cards, one is the "goal suit" worth 10/card at game end. Players openly bid/offer cards to each other trying to deduce which suit is valuable and accumulate it. Pure information extraction + trading speed. The canonical quant intern game.

## 2. Market Making
A mystery asset has a true value revealed at the end. Each round, players post bid/ask quotes. An NPC "informed trader" sometimes hits your quote. Tight spreads = more flow but more adverse selection risk. Widest P&L wins. Teaches market microstructure.

## 3. Options Pricing
Flash a scenario (underlying price, vol, time to expiry, strike) and players estimate the fair option value. Closest to Black-Scholes wins. Variants: "which of these 3 options is mispriced?", or Greeks estimation.

## 4. Colonel Blotto (Web)
Allocate N soldiers across K battlefields (hidden, simultaneous). Whoever has more on a battlefield wins it. Most battlefields won = overall winner. Simple rules, absurdly deep mixed-strategy Nash equilibria. **Build as a web game** — drag-and-drop or slider UI for allocating troops across battlefields, animated reveal phase showing each battlefield's result. Follows the Figgie/Trading Floor WebSocket pattern (Discord for betting/coordination, browser for gameplay).

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

## 16. Liar's Dice
2-6 players, each starts with 5 hidden dice. On your turn, make a claim about the *total* dice in play across all players (e.g. "there are at least four 5s"). Next player must raise the bid (higher quantity or higher face) or call "liar." If called: count all dice — if the bid was met, the caller loses a die; if not, the bidder loses one. Last player with dice wins. Pure probability estimation + bluffing. Web UI: show your own dice, hide others, bid panel with quantity/face selectors, "Liar!" button. Rounds are fast (~5 min). Perfect for the betting crowd — you're literally estimating hidden distributions.

## 17. Minesweeper Racing
Same concept as Sudoku Racing. All players get the *same* randomly generated Minesweeper board (e.g. 9x9, 10 mines). First to clear the board wins. Classic left-click to reveal, right-click to flag. Web UI: interactive grid, timer, live progress bars showing how many safe cells each player has revealed. Ties broken by time. Proven racing format — minimal new infra since it follows the Sudoku room/session pattern exactly. Difficulty selector: Easy (9x9, 10 mines), Medium (16x16, 40), Hard (16x30, 99).

## 18. Wits & Wagers
Trivia game where the answer is always a number ("How many bones in the human body?"). Everyone submits a guess, then all guesses are revealed and ranked. Players then *bet coins* on which guess is closest to the actual answer. Correct bettors split the pot. Separates knowledge from betting skill — you don't need to know the answer, you just need to evaluate who does. 8-10 questions per game. Source from a curated question bank of numerical trivia.

## 19. Calibration Challenge
Given a factual question with a numeric answer ("What is the population of Portugal?"), submit a 90% confidence interval (lower bound, upper bound). Scored on calibration: intervals that contain the true answer earn points, but narrower intervals earn *more* points. Over many rounds, the best-calibrated player wins. Penalizes both overconfidence (too narrow, misses) and underconfidence (too wide, low score). Literally trains the core skill of quantitative reasoning — knowing what you don't know.

## 20. Skull
3-6 players. Each has 3 roses and 1 skull (face-down coasters). Each round: players take turns placing one disc face-down in their stack. After at least one each, any player can bid instead of placing — "I can flip X discs without hitting a skull." Bidding goes up until one person holds the highest bid. That player flips discs (must flip all of their own first, then choose from others). Hit a skull = lose a disc permanently. Successfully flip your bid = win a point. 2 points to win. Pure bluffing — you know where *your* skull is but not theirs. Minimal state, fast rounds, incredible mind games.

## 21. Type Racer
All players get the same passage (60-100 words). Type it out as fast and accurately as possible. Real-time progress bars show everyone's position. WPM + accuracy = score. Passages sourced from a curated bank (famous quotes, code snippets, sports commentary). Simple to build, infinitely replayable, very competitive. Variants: code-only mode (type actual Python/JS), backwards mode, emoji mode.

## 22. Crossword Racing
Same mini crossword (5x5 or 7x7) for all players, race to complete it. Clues displayed alongside the grid. Interactive web grid with tab/arrow navigation. First to fill all squares correctly wins. Source puzzles from a curated bank or generate procedurally. Follows the Sudoku/Minesweeper racing pattern.

## 23. No Thanks
3-7 players. Cards numbered 3-35 shuffled, 9 removed secretly. Each turn, flip a card. In order, players either take the card (adding its value to their score) or pay 1 chip to pass. Chips on a refused card accumulate — whoever finally takes it gets all the chips too. Consecutive cards in your hand form a "run" and only the lowest counts. Chips subtract from score. Lowest score wins. Simple rules, agonizing decisions. Every card is a mini-auction. Teaches opportunity cost and sunk cost reasoning.

## 24. Horse Racing Sim
Simulated horse races with AI horses. Each horse has hidden stats (speed, stamina, consistency) plus randomness. Before each race, players see a form guide (past finishes, odds board) and place bets from their coin balance. 6-8 horses per race, 4-5 races per session. Animated race visualization on web (horses moving across the track). Payouts based on parimutuel odds (pool-based, like real horse racing). Between races, sharp players update their models of each horse. Basically a micro sports-betting simulator.

## 25. Estimation Game (Fermi Problems)
"How many piano tuners are in Chicago?" — classic Fermi estimation. Players submit a number. Closest order of magnitude wins. Scoring: exact order of magnitude = 3 pts, within one order = 1 pt. 10 rounds, escalating difficulty. Trains the same decomposition-and-estimation muscle used in quant interviews. Questions sourced from a curated bank mixing physics, geography, economics, pop culture.

## 26. Avalon (Resistance)
5-10 players. Secret roles: some are loyal servants of Arthur, some are Merlin's spies (evil). Each round, a leader proposes a team to go on a quest. Everyone votes approve/reject. If approved, team members secretly play success/fail. Spies can sabotage. Loyalists must deduce who the spies are through voting patterns and quest results. If Merlin is in play, they know who's evil but must stay hidden. Social deduction at its finest — requires reading people through their *actions*, not just words.

## 27. Snake Battle
Multiplayer snake on a shared grid. Eat food to grow, avoid walls and other snakes. Last snake alive wins. Power-ups: speed boost, shield, reverse controls. Web-native, real-time via WebSocket. Simple to build, visually exciting, surprisingly strategic (cutting off opponents, controlling space). 2-6 players. Rounds are fast (~60s).

## 28. Portfolio Optimizer
Each player is a fund manager. Given a universe of ~10 assets with historical return data (displayed as charts), allocate a portfolio (weights summing to 100%). Then a "future period" is simulated. Highest Sharpe ratio wins (not just raw return — risk matters). 3 rounds with different market regimes (bull, bear, volatile). Teaches Modern Portfolio Theory intuitively. The quant crowd will love arguing about correlations.
