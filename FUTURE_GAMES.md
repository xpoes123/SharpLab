# Future Games — Build Later

Ideas for math/logic/quant-heavy games to add to the casino.

## 1. Market Making
A mystery asset has a true value revealed at the end. Each round, players post bid/ask quotes. An NPC "informed trader" sometimes hits your quote. Tight spreads = more flow but more adverse selection risk. Widest P&L wins. Teaches market microstructure.

## 2. Options Pricing
Flash a scenario (underlying price, vol, time to expiry, strike) and players estimate the fair option value. Closest to Black-Scholes wins. Variants: "which of these 3 options is mispriced?", or Greeks estimation.

## 3. Colonel Blotto (Web)
Allocate N soldiers across K battlefields (hidden, simultaneous). Whoever has more on a battlefield wins it. Most battlefields won = overall winner. Simple rules, absurdly deep mixed-strategy Nash equilibria. **Build as a web game** — drag-and-drop or slider UI for allocating troops across battlefields, animated reveal phase showing each battlefield's result. Follows the Figgie/Trading Floor WebSocket pattern (Discord for betting/coordination, browser for gameplay).

## 4. Prisoner's Dilemma Tournament
Iterated PD, ~20 rounds. Players submit a strategy (cooperate/defect each round, can condition on opponent's history). Tit-for-tat, grim trigger, etc. Round-robin tournament, highest cumulative payoff wins.

## 5. Sequence
Show 4-6 terms of a mathematical sequence, guess the next term. Mix OEIS-style integer sequences with pattern recognition. Harder rounds give fewer terms. Speed + math intuition.

## 6. Mental Math Sprint
10 rapid-fire problems per round (3-digit multiplication, quick combinatorics, percentage estimation, etc). Fastest correct answer wins each problem. Can run as tournament bracket.

## 7. Connect 4
7 columns, drop pieces, first to connect 4 in a row/column/diagonal wins. Render board in monospace. 1v1 — perfect for duels. Coordinate input via buttons (column select). Surprisingly strategic despite simple rules.

## 8. Battleship
Hidden board, place ships, take turns firing. Probability-driven search — optimal play is entropy-maximizing. Render two boards (yours + tracking). 1v1 only. Great test of systematic search vs. intuition.

## 9. Bayesian Detective
A crime happened. 5 suspects, each with a prior probability. Each round reveals a clue (evidence with known likelihood ratios) and players update their posterior distribution over suspects. Submit your probability distribution. Scored by Brier score / log loss — perfect calibration wins. Literally trains Bayesian reasoning.

## 10. Coup
Bluffing card game. Each player has 2 influence cards (hidden). On your turn, claim any role's action (Duke: tax, Assassin: assassinate, Captain: steal, etc). Anyone can challenge your claim — if you're bluffing, you lose a card; if you're telling the truth, the challenger loses one. Last player with influence wins. Mixed-strategy equilibrium game — bluff frequency is the skill expression.

## 11. Indian Poker (Sit & Go)
Each player gets one card face-up on their "forehead" — everyone can see everyone else's card but not their own. Betting rounds like poker: check, bet, raise, fold. You infer your card's strength from how others bet. Sit-and-go format: fixed buy-in, play until one player has all the chips. Classic information asymmetry game.

## 12. March Madness Bracket
8-team single-elimination bracket using real NCAA basketball teams. Each game draws from a curated pool of top programs (Duke, UConn, Kansas, Gonzaga, Kentucky, etc.) and assigns three ratings: **Offense** (scoring ability), **Defense** (stops), and **Coaching** (adjustments/clutch factor). Ratings are randomized within a plausible range each tournament so no bracket is the same twice.

**Simulation:** Matchups resolved probabilistically — offense vs. opposing defense produces an expected scoring differential, coaching acts as a multiplier in close games. Higher composite overall = higher win probability, but upsets happen (weighted randomness, not deterministic).

**Solo play:** Pick all 7 winners across 3 rounds (quarters → semis → final). Score = correct picks, weighted by round (1 pt first round, 2 pts semis, 4 pts final). Leaderboard tracks best scores.

**Multiplayer:** All players fill out brackets simultaneously (hidden until lock). After lock, the sim runs live round-by-round with reveals. Payout ladder: last place gets nothing, scales up to winner. Ties broken by closest-to-actual total points in the final.

**Key design notes:**
- Show team ratings before bracket lock so picks are informed, not random
- Render bracket as monospace ASCII or embed image
- Each round reveal is a separate message for suspense
- Side pots: optional "upset special" bonus for correctly calling a lower-seed win

## 13. Lyric Guesser
Play a snippet of song lyrics (3-4 lines, censoring the title/artist name if it appears). Players guess the song title and/or artist. Faster correct answer = more points. Rounds escalate: first hint is a deep-cut line, second is the chorus, third is a dead giveaway. Can theme rounds (90s hip-hop, pop hits, etc.). Source lyrics from a curated bank to avoid API/copyright issues. Solo or multiplayer — works great as a timed sprint.

## 14. NBA Silhouette Guesser
Show a blacked-out silhouette of an NBA player (action shot — dunk, jumper, celebration) and players guess who it is. Progressive hints: silhouette only → add jersey number → add team colors → reveal partial face. Fewer hints needed = more points. Source from a curated image bank of ~100+ recognizable players. Variant: guess the era (70s/80s/90s/00s/10s/20s) from the silhouette style. Great sports knowledge test.

## 15. Liar's Dice (Web)
2-6 players, each starts with 5 hidden dice. On your turn, make a claim about the *total* dice in play across all players (e.g. "there are at least four 5s"). Next player must raise the bid (higher quantity or higher face) or call "liar." If called: count all dice — if the bid was met, the caller loses a die; if not, the bidder loses one. Last player with dice wins. Pure probability estimation + bluffing. Web UI: show your own dice, hide others, bid panel with quantity/face selectors, "Liar!" button. Rounds are fast (~5 min). Perfect for the betting crowd — you're literally estimating hidden distributions.

## 16. Minesweeper Racing (Web)
Same concept as Sudoku Racing. All players get the *same* randomly generated Minesweeper board (e.g. 9x9, 10 mines). First to clear the board wins. Classic left-click to reveal, right-click to flag. Web UI: interactive grid, timer, live progress bars showing how many safe cells each player has revealed. Ties broken by time. Proven racing format — minimal new infra since it follows the Sudoku room/session pattern exactly. Difficulty selector: Easy (9x9, 10 mines), Medium (16x16, 40), Hard (16x30, 99).

## 17. Skull
3-6 players. Each has 3 roses and 1 skull (face-down coasters). Each round: players take turns placing one disc face-down in their stack. After at least one each, any player can bid instead of placing — "I can flip X discs without hitting a skull." Bidding goes up until one person holds the highest bid. That player flips discs (must flip all of their own first, then choose from others). Hit a skull = lose a disc permanently. Successfully flip your bid = win a point. 2 points to win. Pure bluffing — you know where *your* skull is but not theirs. Minimal state, fast rounds, incredible mind games.

## 18. No Thanks
3-7 players. Cards numbered 3-35 shuffled, 9 removed secretly. Each turn, flip a card. In order, players either take the card (adding its value to their score) or pay 1 chip to pass. Chips on a refused card accumulate — whoever finally takes it gets all the chips too. Consecutive cards in your hand form a "run" and only the lowest counts. Chips subtract from score. Lowest score wins. Simple rules, agonizing decisions. Every card is a mini-auction. Teaches opportunity cost and sunk cost reasoning.

## 19. Horse Racing Sim (Web)
Simulated horse races with AI horses. Each horse has hidden stats (speed, stamina, consistency) plus randomness. Before each race, players see a form guide (past finishes, odds board) and place bets from their coin balance. 6-8 horses per race, 4-5 races per session. Animated race visualization on web (horses moving across the track). Payouts based on parimutuel odds (pool-based, like real horse racing). Between races, sharp players update their models of each horse. Basically a micro sports-betting simulator.

## 20. Avalon (Resistance)
5-10 players. Secret roles: some are loyal servants of Arthur, some are Merlin's spies (evil). Each round, a leader proposes a team to go on a quest. Everyone votes approve/reject. If approved, team members secretly play success/fail. Spies can sabotage. Loyalists must deduce who the spies are through voting patterns and quest results. If Merlin is in play, they know who's evil but must stay hidden. Social deduction at its finest — requires reading people through their *actions*, not just words.

## 21. Pursuit-Evasion (Web)
Graph-based cat-and-mouse. One player is the **evader**, others are **pursuers** on a shared graph. Alternating turns: evader moves first, then all pursuers move simultaneously. Evader wins by surviving N turns; pursuers win by landing on the evader's node. **Fog of war** — evader only sees adjacent nodes, pursuers only see within radius 2. Graph topology is the strategy space: dead ends are traps, high-degree nodes are escape routes, cut vertices are chokepoints. Web UI: interactive graph visualization (force-directed layout), click nodes to move, fog fades in/out smoothly. Multiple graph types: grid, random planar, tree, small-world. Scales from 1v1 to 3v1. Teaches graph connectivity, search strategies, and spatial reasoning. Fast rounds (~2 min per graph).

## 22. Lights Out (Web)
NxN grid of lights, each on or off. Toggling a light flips it **and all orthogonal neighbors**. Goal: turn all lights off from a random starting state. The twist: not all configurations are solvable — the solution space is a system of linear equations over GF(2). Multiplayer mode: all players get the *same* starting board, race to solve. Fewer moves = better score (ties broken by time). Difficulty scales with grid size: 3x3 (easy, always solvable), 5x5 (classic, some configs unsolvable — must identify and skip), 7x7 (hard). Web UI: clickable grid with smooth light toggle animations, move counter, opponent progress bars. Variant: "target pattern" mode — reach a specific configuration instead of all-off. Teaches linear algebra intuitively — players who recognize the null space structure dominate.
