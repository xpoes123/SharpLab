"""NBA Player Guess — name the player from their career teams."""

import asyncio
import random
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs._elo_helpers import fmt_elo_change, update_elo_multiplayer
import logging

log = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────────────

ROUND_TIME = 45       # total round time in seconds
CLUE_INTERVAL = 8     # seconds between team reveals
ROUND_DELAY = 4       # seconds between rounds
MIN_PLAYERS = 1
MAX_PLAYERS = 8
DEFAULT_ROUNDS = 10
MAX_ROUNDS_CAP = 20
INACTIVITY_ROUNDS = 5  # auto-end after N consecutive unanswered rounds

MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]

# -- Player Data (nba_api) ----------------------------------------------------
# (id, name, [alts], [(team, start_year, end_year), ...], {ppg, rpg, apg, gp})
# Generated from nba_api — 101 players

NBA_PLAYERS_DATA: list[tuple[int, str, list[str], list[tuple[str, int, int]], dict]] = [
    (1, 'LeBron James', ['LeBron', 'Bron', 'King James'], [('Cavaliers', 2003, 2010), ('Heat', 2010, 2014), ('Cavaliers', 2014, 2018), ('Lakers', 2018, 2026)], {'ppg': 26.8, 'rpg': 7.5, 'apg': 7.4, 'gp': 1622}),
    (2, 'Stephen Curry', ['Steph Curry', 'Steph', 'Chef Curry'], [('Warriors', 2009, 2026)], {'ppg': 24.8, 'rpg': 4.7, 'apg': 6.3, 'gp': 1069}),
    (3, 'Kevin Durant', ['KD', 'Durant'], [('Supersonics', 2007, 2008), ('Thunder', 2008, 2016), ('Warriors', 2016, 2019), ('Nets', 2020, 2023), ('Suns', 2022, 2025), ('Rockets', 2025, 2026)], {'ppg': 27.1, 'rpg': 6.9, 'apg': 4.4, 'gp': 1201}),
    (4, 'James Harden', ['Harden', 'The Beard'], [('Thunder', 2009, 2012), ('Rockets', 2012, 2021), ('Nets', 2020, 2022), ('76ers', 2021, 2023), ('Clippers', 2023, 2026), ('Cavaliers', 2025, 2026)], {'ppg': 24.0, 'rpg': 5.6, 'apg': 7.3, 'gp': 1221}),
    (5, 'Chris Paul', ['CP3'], [('Hornets', 2005, 2011), ('Clippers', 2011, 2017), ('Rockets', 2017, 2019), ('Thunder', 2019, 2020), ('Suns', 2020, 2023), ('Warriors', 2023, 2024), ('Spurs', 2024, 2025), ('Clippers', 2025, 2026)], {'ppg': 16.8, 'rpg': 4.4, 'apg': 9.2, 'gp': 1370}),
    (6, 'Jimmy Butler', ['Jimmy Buckets', 'Jimmy'], [('Bulls', 2011, 2017), ('Timberwolves', 2017, 2019), ('76ers', 2018, 2019), ('Heat', 2019, 2025), ('Warriors', 2024, 2026)], {'ppg': 18.4, 'rpg': 5.4, 'apg': 4.4, 'gp': 907}),
    (7, 'Kawhi Leonard', ['Kawhi', 'The Claw'], [('Spurs', 2011, 2018), ('Raptors', 2018, 2019), ('Clippers', 2019, 2026)], {'ppg': 20.7, 'rpg': 6.4, 'apg': 3.1, 'gp': 798}),
    (8, 'Paul George', ['PG13', 'PG'], [('Pacers', 2010, 2017), ('Thunder', 2017, 2019), ('Clippers', 2019, 2024), ('76ers', 2024, 2026)], {'ppg': 20.5, 'rpg': 6.2, 'apg': 3.7, 'gp': 945}),
    (9, 'Damian Lillard', ['Dame', 'Dame Lillard', 'Dame Time'], [('Trail Blazers', 2012, 2023), ('Bucks', 2023, 2025)], {'ppg': 25.1, 'rpg': 4.3, 'apg': 6.7, 'gp': 900}),
    (10, 'Bradley Beal', ['Beal'], [('Wizards', 2012, 2023), ('Suns', 2023, 2025), ('Clippers', 2025, 2026)], {'ppg': 21.4, 'rpg': 4.0, 'apg': 4.3, 'gp': 807}),
    (11, 'Draymond Green', ['Draymond'], [('Warriors', 2012, 2026)], {'ppg': 8.7, 'rpg': 6.8, 'apg': 5.6, 'gp': 949}),
    (12, 'Joel Embiid', ['Embiid', 'The Process'], [('76ers', 2016, 2026)], {'ppg': 27.6, 'rpg': 10.8, 'apg': 3.7, 'gp': 490}),
    (13, 'Giannis Antetokounmpo', ['Giannis', 'Greek Freak'], [('Bucks', 2013, 2026)], {'ppg': 24.1, 'rpg': 9.9, 'apg': 5.0, 'gp': 895}),
    (14, 'Russell Westbrook', ['Russ', 'Westbrook', 'Brodie'], [('Thunder', 2008, 2019), ('Rockets', 2019, 2020), ('Wizards', 2020, 2021), ('Lakers', 2021, 2023), ('Clippers', 2022, 2024), ('Nuggets', 2024, 2025), ('Kings', 2025, 2026)], {'ppg': 20.9, 'rpg': 6.9, 'apg': 8.0, 'gp': 1301}),
    (15, 'Kyle Lowry', ['Lowry'], [('Grizzlies', 2006, 2009), ('Rockets', 2008, 2012), ('Raptors', 2012, 2021), ('Heat', 2021, 2024), ('76ers', 2023, 2026)], {'ppg': 13.8, 'rpg': 4.2, 'apg': 6.0, 'gp': 1187}),
    (16, 'DeMar DeRozan', ['DeRozan', 'DeMar'], [('Raptors', 2009, 2018), ('Spurs', 2018, 2021), ('Bulls', 2021, 2024), ('Kings', 2024, 2026)], {'ppg': 21.1, 'rpg': 4.3, 'apg': 4.1, 'gp': 1264}),
    (17, 'Al Horford', ['Horford'], [('Hawks', 2007, 2016), ('Celtics', 2016, 2019), ('76ers', 2019, 2020), ('Thunder', 2020, 2021), ('Celtics', 2021, 2025), ('Warriors', 2025, 2026)], {'ppg': 12.7, 'rpg': 7.7, 'apg': 3.2, 'gp': 1183}),
    (18, 'Brook Lopez', ['Brook'], [('Nets', 2008, 2017), ('Lakers', 2017, 2018), ('Bucks', 2018, 2025), ('Clippers', 2025, 2026)], {'ppg': 15.4, 'rpg': 5.9, 'apg': 1.4, 'gp': 1180}),
    (19, 'Jrue Holiday', ['Jrue'], [('76ers', 2009, 2013), ('Pelicans', 2013, 2020), ('Bucks', 2020, 2023), ('Celtics', 2023, 2025), ('Trail Blazers', 2025, 2026)], {'ppg': 15.9, 'rpg': 4.2, 'apg': 6.2, 'gp': 1090}),
    (20, 'Derrick Rose', ['D-Rose', 'Rose'], [('Bulls', 2008, 2016), ('Knicks', 2016, 2017), ('Cavaliers', 2017, 2018), ('Timberwolves', 2017, 2019), ('Pistons', 2019, 2021), ('Knicks', 2020, 2023), ('Grizzlies', 2023, 2024)], {'ppg': 17.4, 'rpg': 3.2, 'apg': 5.2, 'gp': 723}),
    (21, 'Mike Conley', ['Conley'], [('Grizzlies', 2007, 2019), ('Jazz', 2019, 2023), ('Timberwolves', 2022, 2026)], {'ppg': 13.6, 'rpg': 2.9, 'apg': 5.5, 'gp': 1226}),
    (22, 'Marcus Smart', ['Smart'], [('Celtics', 2014, 2023), ('Grizzlies', 2023, 2025), ('Wizards', 2024, 2025), ('Lakers', 2025, 2026)], {'ppg': 10.5, 'rpg': 3.4, 'apg': 4.4, 'gp': 697}),
    (23, 'Khris Middleton', ['Middleton'], [('Pistons', 2012, 2013), ('Bucks', 2013, 2025), ('Wizards', 2024, 2026), ('Mavericks', 2025, 2026)], {'ppg': 16.1, 'rpg': 4.7, 'apg': 3.9, 'gp': 839}),
    (24, 'Tobias Harris', ['Tobias'], [('Bucks', 2011, 2013), ('Magic', 2012, 2016), ('Pistons', 2015, 2018), ('Clippers', 2017, 2019), ('76ers', 2018, 2024), ('Pistons', 2024, 2026)], {'ppg': 15.9, 'rpg': 6.1, 'apg': 2.4, 'gp': 1033}),
    (25, 'Andre Drummond', ['Drummond'], [('Pistons', 2012, 2020), ('Cavaliers', 2019, 2021), ('Lakers', 2020, 2021), ('76ers', 2021, 2022), ('Nets', 2021, 2022), ('Bulls', 2022, 2024), ('76ers', 2024, 2026)], {'ppg': 12.1, 'rpg': 11.9, 'apg': 1.2, 'gp': 967}),
    (26, 'Clint Capela', ['Capela'], [('Rockets', 2014, 2020), ('Hawks', 2020, 2025), ('Rockets', 2025, 2026)], {'ppg': 11.1, 'rpg': 9.9, 'apg': 1.0, 'gp': 739}),
    (27, 'Steven Adams', ['Adams'], [('Thunder', 2013, 2020), ('Pelicans', 2020, 2021), ('Grizzlies', 2021, 2023), ('Rockets', 2024, 2026)], {'ppg': 8.7, 'rpg': 8.0, 'apg': 1.5, 'gp': 796}),
    (28, 'Julius Randle', ['Randle'], [('Lakers', 2014, 2018), ('Pelicans', 2018, 2019), ('Knicks', 2019, 2024), ('Timberwolves', 2024, 2026)], {'ppg': 19.2, 'rpg': 8.9, 'apg': 3.9, 'gp': 789}),
    (29, 'Buddy Hield', ['Buddy'], [('Pelicans', 2016, 2017), ('Kings', 2016, 2022), ('Pacers', 2021, 2024), ('76ers', 2023, 2024), ('Warriors', 2024, 2026), ('Hawks', 2025, 2026)], {'ppg': 14.5, 'rpg': 4.0, 'apg': 2.4, 'gp': 765}),
    (30, 'Myles Turner', ['Turner'], [('Pacers', 2015, 2025), ('Bucks', 2025, 2026)], {'ppg': 13.9, 'rpg': 6.6, 'apg': 1.3, 'gp': 713}),
    (31, 'CJ McCollum', ['CJ'], [('Trail Blazers', 2013, 2022), ('Pelicans', 2021, 2025), ('Wizards', 2025, 2026), ('Hawks', 2025, 2026)], {'ppg': 19.5, 'rpg': 3.6, 'apg': 3.8, 'gp': 863}),
    (32, 'Zach LaVine', ['LaVine'], [('Timberwolves', 2014, 2017), ('Bulls', 2017, 2025), ('Kings', 2024, 2026)], {'ppg': 20.7, 'rpg': 4.0, 'apg': 3.9, 'gp': 693}),
    (33, 'Terry Rozier', ['Scary Terry', 'Rozier'], [('Celtics', 2015, 2019), ('Hornets', 2019, 2024), ('Heat', 2023, 2025)], {'ppg': 13.9, 'rpg': 3.9, 'apg': 3.5, 'gp': 665}),
    (34, 'Aaron Gordon', ['AG', 'Gordon'], [('Magic', 2014, 2021), ('Nuggets', 2020, 2026)], {'ppg': 13.7, 'rpg': 6.2, 'apg': 2.7, 'gp': 756}),
    (35, 'Rudy Gobert', ['Gobert', 'The Stifle Tower'], [('Jazz', 2013, 2022), ('Timberwolves', 2022, 2026)], {'ppg': 12.5, 'rpg': 11.7, 'apg': 1.4, 'gp': 905}),
    (36, 'Donovan Mitchell', ['Spida', 'Mitchell'], [('Jazz', 2017, 2022), ('Cavaliers', 2022, 2026)], {'ppg': 25.1, 'rpg': 4.3, 'apg': 4.8, 'gp': 609}),
    (37, 'Pascal Siakam', ['Siakam', 'Spicy P'], [('Raptors', 2016, 2024), ('Pacers', 2023, 2026)], {'ppg': 18.5, 'rpg': 6.6, 'apg': 3.6, 'gp': 691}),
    (38, 'Fred VanVleet', ['FVV', 'VanVleet'], [('Raptors', 2016, 2023), ('Rockets', 2023, 2025)], {'ppg': 14.9, 'rpg': 3.4, 'apg': 5.7, 'gp': 550}),
    (39, 'Andrew Wiggins', ['Wiggins'], [('Timberwolves', 2014, 2020), ('Warriors', 2019, 2025), ('Heat', 2024, 2026)], {'ppg': 18.2, 'rpg': 4.5, 'apg': 2.3, 'gp': 834}),
    (40, "D'Angelo Russell", ['DLo', "D'Angelo"], [('Lakers', 2015, 2017), ('Nets', 2017, 2019), ('Warriors', 2019, 2020), ('Timberwolves', 2019, 2023), ('Lakers', 2022, 2025), ('Nets', 2024, 2025), ('Mavericks', 2025, 2026)], {'ppg': 17.0, 'rpg': 3.3, 'apg': 5.6, 'gp': 655}),
    (41, 'Spencer Dinwiddie', ['Dinwiddie'], [('Pistons', 2014, 2016), ('Nets', 2016, 2021), ('Wizards', 2021, 2022), ('Mavericks', 2021, 2023), ('Nets', 2022, 2024), ('Lakers', 2023, 2024), ('Mavericks', 2024, 2025)], {'ppg': 13.0, 'rpg': 3.0, 'apg': 5.1, 'gp': 621}),
    (42, 'Kemba Walker', ['Kemba'], [('Hornets', 2011, 2019), ('Celtics', 2019, 2021), ('Knicks', 2021, 2022), ('Mavericks', 2022, 2023)], {'ppg': 19.3, 'rpg': 3.8, 'apg': 5.3, 'gp': 750}),
    (43, 'John Collins', ['Collins'], [('Hawks', 2017, 2023), ('Jazz', 2023, 2025), ('Clippers', 2025, 2026)], {'ppg': 15.7, 'rpg': 7.7, 'apg': 1.4, 'gp': 541}),
    (44, 'Eric Gordon', ['EG'], [('Clippers', 2008, 2011), ('Hornets', 2011, 2013), ('Pelicans', 2013, 2016), ('Rockets', 2016, 2023), ('Clippers', 2022, 2023), ('Suns', 2023, 2024), ('76ers', 2024, 2026)], {'ppg': 15.2, 'rpg': 2.3, 'apg': 2.7, 'gp': 931}),
    (45, 'Reggie Jackson', ['Reggie'], [('Thunder', 2011, 2015), ('Pistons', 2014, 2020), ('Clippers', 2019, 2023), ('Nuggets', 2022, 2024), ('76ers', 2024, 2025)], {'ppg': 12.3, 'rpg': 2.8, 'apg': 4.1, 'gp': 884}),
    (46, 'Harrison Barnes', ['Barnes'], [('Warriors', 2012, 2016), ('Mavericks', 2016, 2019), ('Kings', 2018, 2024), ('Spurs', 2024, 2026)], {'ppg': 13.6, 'rpg': 4.6, 'apg': 1.8, 'gp': 1070}),
    (47, 'Kentavious Caldwell-Pope', ['KCP'], [('Pistons', 2013, 2017), ('Lakers', 2017, 2021), ('Wizards', 2021, 2022), ('Nuggets', 2022, 2024), ('Magic', 2024, 2025), ('Grizzlies', 2025, 2026)], {'ppg': 11.0, 'rpg': 2.9, 'apg': 1.9, 'gp': 963}),
    (48, 'Tim Hardaway Jr.', ['THJ', 'Hardaway Jr'], [('Knicks', 2013, 2015), ('Hawks', 2015, 2017), ('Knicks', 2017, 2019), ('Mavericks', 2018, 2024), ('Pistons', 2024, 2025), ('Nuggets', 2025, 2026)], {'ppg': 13.7, 'rpg': 2.8, 'apg': 1.8, 'gp': 893}),
    (49, 'Robert Covington', ['RoCo', 'Covington'], [('Rockets', 2013, 2014), ('76ers', 2014, 2019), ('Timberwolves', 2018, 2020), ('Rockets', 2019, 2020), ('Trail Blazers', 2020, 2022), ('Clippers', 2021, 2024), ('76ers', 2023, 2024)], {'ppg': 10.8, 'rpg': 5.5, 'apg': 1.4, 'gp': 614}),
    (50, 'Caris LeVert', ['LeVert'], [('Nets', 2016, 2021), ('Pacers', 2020, 2022), ('Cavaliers', 2021, 2025), ('Hawks', 2024, 2025), ('Pistons', 2025, 2026)], {'ppg': 13.2, 'rpg': 3.6, 'apg': 3.9, 'gp': 584}),
    (51, 'Norman Powell', ['Norm Powell', 'Powell'], [('Raptors', 2015, 2021), ('Trail Blazers', 2020, 2022), ('Clippers', 2021, 2025), ('Heat', 2025, 2026)], {'ppg': 14.0, 'rpg': 2.7, 'apg': 1.6, 'gp': 675}),
    (52, 'Kelly Oubre Jr.', ['Oubre'], [('Wizards', 2015, 2019), ('Suns', 2018, 2020), ('Warriors', 2020, 2021), ('Hornets', 2021, 2023), ('76ers', 2023, 2026)], {'ppg': 13.3, 'rpg': 4.7, 'apg': 1.2, 'gp': 705}),
    (53, 'Marcus Morris Sr.', ['Marcus Morris'], [('Rockets', 2011, 2013), ('Suns', 2012, 2015), ('Pistons', 2015, 2017), ('Celtics', 2017, 2019), ('Knicks', 2019, 2020), ('Clippers', 2019, 2023), ('76ers', 2023, 2024), ('Cavaliers', 2023, 2024)], {'ppg': 12.0, 'rpg': 4.4, 'apg': 1.5, 'gp': 832}),
    (54, 'Montrezl Harrell', ['Trezz', 'Harrell'], [('Rockets', 2015, 2017), ('Clippers', 2017, 2020), ('Lakers', 2020, 2021), ('Wizards', 2021, 2022), ('Hornets', 2021, 2022), ('76ers', 2022, 2023)], {'ppg': 12.1, 'rpg': 5.0, 'apg': 1.3, 'gp': 515}),
    (55, 'Bojan Bogdanovic', ['Bojan', 'Bogdanovic'], [('Nets', 2014, 2017), ('Wizards', 2016, 2017), ('Pacers', 2017, 2019), ('Jazz', 2019, 2022), ('Pistons', 2022, 2024), ('Knicks', 2023, 2024)], {'ppg': 15.6, 'rpg': 3.6, 'apg': 1.7, 'gp': 719}),
    (56, 'Gordon Hayward', ['Hayward'], [('Jazz', 2010, 2017), ('Celtics', 2017, 2020), ('Hornets', 2020, 2024), ('Thunder', 2023, 2024)], {'ppg': 15.2, 'rpg': 4.4, 'apg': 3.5, 'gp': 835}),
    (57, 'LaMarcus Aldridge', ['LMA', 'Aldridge'], [('Trail Blazers', 2006, 2015), ('Spurs', 2015, 2021), ('Nets', 2020, 2022)], {'ppg': 19.1, 'rpg': 8.1, 'apg': 1.9, 'gp': 1076}),
    (58, 'Andre Iguodala', ['Iggy', 'Iguodala'], [('76ers', 2004, 2012), ('Nuggets', 2012, 2013), ('Warriors', 2013, 2019), ('Heat', 2019, 2021), ('Warriors', 2021, 2023)], {'ppg': 11.3, 'rpg': 4.9, 'apg': 4.2, 'gp': 1231}),
    (59, 'Dwight Howard', ['Dwight', 'Superman'], [('Magic', 2004, 2012), ('Lakers', 2012, 2013), ('Rockets', 2013, 2016), ('Hawks', 2016, 2017), ('Hornets', 2017, 2018), ('Wizards', 2018, 2019), ('Lakers', 2019, 2020), ('76ers', 2020, 2021), ('Lakers', 2021, 2022)], {'ppg': 15.7, 'rpg': 11.8, 'apg': 1.3, 'gp': 1242}),
    (60, 'Danilo Gallinari', ['Gallo', 'Gallinari'], [('Knicks', 2008, 2011), ('Nuggets', 2010, 2017), ('Clippers', 2017, 2019), ('Thunder', 2019, 2020), ('Hawks', 2020, 2022), ('Wizards', 2023, 2024), ('Pistons', 2023, 2024), ('Bucks', 2023, 2024)], {'ppg': 14.9, 'rpg': 4.7, 'apg': 1.9, 'gp': 777}),
    (61, 'JJ Redick', ['Redick'], [('Magic', 2006, 2013), ('Bucks', 2012, 2013), ('Clippers', 2013, 2017), ('76ers', 2017, 2019), ('Pelicans', 2019, 2021), ('Mavericks', 2020, 2021)], {'ppg': 12.8, 'rpg': 2.0, 'apg': 2.0, 'gp': 940}),
    (62, 'Jarrett Allen', ['Allen'], [('Nets', 2017, 2021), ('Cavaliers', 2020, 2026)], {'ppg': 13.1, 'rpg': 9.2, 'apg': 1.7, 'gp': 624}),
    (63, 'Domantas Sabonis', ['Sabonis'], [('Thunder', 2016, 2017), ('Pacers', 2017, 2022), ('Kings', 2021, 2026)], {'ppg': 16.1, 'rpg': 10.7, 'apg': 4.9, 'gp': 665}),
    (64, 'Malcolm Brogdon', ['Brogdon'], [('Bucks', 2016, 2019), ('Pacers', 2019, 2022), ('Celtics', 2022, 2023), ('Trail Blazers', 2023, 2024), ('Wizards', 2024, 2025)], {'ppg': 15.3, 'rpg': 4.1, 'apg': 4.7, 'gp': 463}),
    (65, "De'Aaron Fox", ['Fox'], [('Kings', 2017, 2025), ('Spurs', 2024, 2026)], {'ppg': 21.1, 'rpg': 3.9, 'apg': 6.1, 'gp': 603}),
    (66, 'Bam Adebayo', ['Bam'], [('Heat', 2017, 2026)], {'ppg': 16.2, 'rpg': 9.0, 'apg': 3.6, 'gp': 640}),
    (67, 'OG Anunoby', ['OG'], [('Raptors', 2017, 2024), ('Knicks', 2023, 2026)], {'ppg': 13.3, 'rpg': 4.5, 'apg': 1.7, 'gp': 559}),
    (68, 'Jaren Jackson Jr.', ['JJJ', 'Jaren Jackson'], [('Grizzlies', 2018, 2026), ('Jazz', 2025, 2026)], {'ppg': 18.6, 'rpg': 5.6, 'apg': 1.5, 'gp': 455}),
    (69, 'Shai Gilgeous-Alexander', ['SGA', 'Shai'], [('Clippers', 2018, 2019), ('Thunder', 2019, 2026)], {'ppg': 25.3, 'rpg': 4.7, 'apg': 5.3, 'gp': 530}),
    (70, 'Dejounte Murray', ['Dejounte'], [('Spurs', 2016, 2022), ('Hawks', 2022, 2024), ('Pelicans', 2024, 2026)], {'ppg': 15.5, 'rpg': 5.8, 'apg': 5.4, 'gp': 517}),
    (71, 'Brandon Ingram', ['BI', 'Ingram'], [('Lakers', 2016, 2019), ('Pelicans', 2019, 2025), ('Raptors', 2025, 2026)], {'ppg': 19.8, 'rpg': 5.2, 'apg': 4.2, 'gp': 572}),
    (72, 'Tyler Herro', ['Herro'], [('Heat', 2019, 2026)], {'ppg': 19.5, 'rpg': 5.0, 'apg': 4.1, 'gp': 394}),
    (73, 'Jamal Murray', ['Murray'], [('Nuggets', 2016, 2026)], {'ppg': 18.9, 'rpg': 3.8, 'apg': 5.0, 'gp': 611}),
    (74, 'Jayson Tatum', ['Tatum', 'JT'], [('Celtics', 2017, 2026)], {'ppg': 23.5, 'rpg': 7.4, 'apg': 3.9, 'gp': 601}),
    (75, 'Jaylen Brown', ['Brown'], [('Celtics', 2016, 2026)], {'ppg': 20.0, 'rpg': 5.5, 'apg': 2.9, 'gp': 674}),
    (76, 'Devin Booker', ['Book', 'Booker', 'D-Book'], [('Suns', 2015, 2026)], {'ppg': 24.6, 'rpg': 4.0, 'apg': 5.3, 'gp': 737}),
    (77, 'Karl-Anthony Towns', ['KAT', 'Towns'], [('Timberwolves', 2015, 2024), ('Knicks', 2024, 2026)], {'ppg': 22.8, 'rpg': 11.1, 'apg': 3.1, 'gp': 720}),
    (78, 'Trae Young', ['Trae', 'Ice Trae'], [('Hawks', 2018, 2026), ('Wizards', 2025, 2026)], {'ppg': 25.1, 'rpg': 3.4, 'apg': 9.8, 'gp': 498}),
    (79, 'Kyrie Irving', ['Kyrie', 'Uncle Drew'], [('Cavaliers', 2011, 2017), ('Celtics', 2017, 2019), ('Nets', 2019, 2023), ('Mavericks', 2022, 2025)], {'ppg': 23.7, 'rpg': 4.1, 'apg': 5.6, 'gp': 779}),
    (80, 'Anthony Davis', ['AD', 'The Brow'], [('Hornets', 2012, 2013), ('Pelicans', 2013, 2019), ('Lakers', 2019, 2025), ('Mavericks', 2024, 2026)], {'ppg': 24.0, 'rpg': 10.7, 'apg': 2.6, 'gp': 807}),
    (81, 'Klay Thompson', ['Klay'], [('Warriors', 2011, 2024), ('Mavericks', 2024, 2026)], {'ppg': 18.6, 'rpg': 3.4, 'apg': 2.2, 'gp': 934}),
    (82, 'Dillon Brooks', ['Brooks'], [('Grizzlies', 2017, 2023), ('Rockets', 2023, 2025), ('Suns', 2025, 2026)], {'ppg': 14.8, 'rpg': 3.3, 'apg': 2.0, 'gp': 548}),
    (83, 'Mitchell Robinson', ['Mitch Rob', 'Robinson'], [('Knicks', 2018, 2026)], {'ppg': 7.5, 'rpg': 8.0, 'apg': 0.7, 'gp': 397}),
    (84, 'Ivica Zubac', ['Zubac'], [('Lakers', 2016, 2019), ('Clippers', 2018, 2026), ('Pacers', 2025, 2026)], {'ppg': 10.5, 'rpg': 8.3, 'apg': 1.4, 'gp': 632}),
    (85, 'P.J. Tucker', ['PJ Tucker', 'Tucker'], [('Raptors', 2006, 2007), ('Suns', 2012, 2017), ('Raptors', 2016, 2017), ('Rockets', 2017, 2021), ('Bucks', 2020, 2021), ('Heat', 2021, 2022), ('76ers', 2022, 2024), ('Clippers', 2023, 2024), ('Knicks', 2024, 2025)], {'ppg': 6.6, 'rpg': 5.4, 'apg': 1.4, 'gp': 886}),
    (86, 'Malik Beasley', ['Beasley'], [('Nuggets', 2016, 2020), ('Timberwolves', 2019, 2022), ('Jazz', 2022, 2023), ('Lakers', 2022, 2023), ('Bucks', 2023, 2024), ('Pistons', 2024, 2025)], {'ppg': 11.7, 'rpg': 2.8, 'apg': 1.4, 'gp': 578}),
    (87, 'Lonzo Ball', ['Zo', 'Lonzo'], [('Lakers', 2017, 2019), ('Pelicans', 2019, 2021), ('Bulls', 2021, 2025), ('Cavaliers', 2025, 2026)], {'ppg': 10.6, 'rpg': 5.3, 'apg': 5.6, 'gp': 322}),
    (88, 'Anthony Edwards', ['Ant', 'Ant-Man'], [('Timberwolves', 2020, 2026)], {'ppg': 24.6, 'rpg': 5.2, 'apg': 4.1, 'gp': 442}),
    (89, 'Victor Wembanyama', ['Wemby', 'Wembanyama'], [('Spurs', 2023, 2026)], {'ppg': 23.4, 'rpg': 11.0, 'apg': 3.5, 'gp': 181}),
    (90, 'Chet Holmgren', ['Chet'], [('Thunder', 2023, 2026)], {'ppg': 16.5, 'rpg': 8.3, 'apg': 2.1, 'gp': 183}),
    (91, 'Paolo Banchero', ['Paolo'], [('Magic', 2022, 2026)], {'ppg': 22.3, 'rpg': 7.4, 'apg': 4.8, 'gp': 270}),
    (92, 'Tyrese Haliburton', ['Haliburton'], [('Kings', 2020, 2022), ('Pacers', 2021, 2025)], {'ppg': 17.5, 'rpg': 3.7, 'apg': 8.8, 'gp': 333}),
    (93, 'Tyrese Maxey', ['Maxey'], [('76ers', 2020, 2026)], {'ppg': 21.1, 'rpg': 3.2, 'apg': 4.8, 'gp': 388}),
    (94, 'Ja Morant', ['Ja'], [('Grizzlies', 2019, 2026)], {'ppg': 22.4, 'rpg': 4.6, 'apg': 7.4, 'gp': 327}),
    (95, 'Scottie Barnes', ['Scottie'], [('Raptors', 2021, 2026)], {'ppg': 17.4, 'rpg': 7.5, 'apg': 5.2, 'gp': 356}),
    (96, 'LaMelo Ball', ['Melo', 'LaMelo'], [('Hornets', 2020, 2026)], {'ppg': 20.8, 'rpg': 5.7, 'apg': 7.3, 'gp': 303}),
    (97, 'Nikola Jokic', ['Jokic', 'The Joker'], [('Nuggets', 2015, 2026)], {'ppg': 22.2, 'rpg': 11.1, 'apg': 7.5, 'gp': 810}),
    (98, 'Nikola Vucevic', ['Vucevic', 'Vooch'], [('76ers', 2011, 2012), ('Magic', 2012, 2021), ('Bulls', 2020, 2026), ('Celtics', 2025, 2026)], {'ppg': 17.1, 'rpg': 10.3, 'apg': 2.9, 'gp': 1036}),
    (99, 'Jonas Valanciunas', ['JV', 'Jonas'], [('Raptors', 2012, 2019), ('Grizzlies', 2018, 2021), ('Pelicans', 2021, 2024), ('Wizards', 2024, 2025), ('Kings', 2024, 2025), ('Nuggets', 2025, 2026)], {'ppg': 12.8, 'rpg': 9.0, 'apg': 1.4, 'gp': 1002}),
    (100, 'Jusuf Nurkic', ['Nurkic', 'The Bosnian Beast'], [('Nuggets', 2014, 2017), ('Trail Blazers', 2016, 2023), ('Suns', 2023, 2025), ('Hornets', 2024, 2025), ('Jazz', 2025, 2026)], {'ppg': 11.8, 'rpg': 8.9, 'apg': 2.6, 'gp': 631}),
    (101, 'Luka Doncic', ['Luka'], [('Mavericks', 2018, 2025), ('Lakers', 2024, 2026)], {'ppg': 29.2, 'rpg': 8.5, 'apg': 8.2, 'gp': 514}),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars."""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c.lower() for c in stripped if c.isalnum()).strip()


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def check_nba_answer(guess: str, entry: tuple) -> bool:
    norm_guess = _normalize(guess)
    if not norm_guess or len(norm_guess) < 3:
        return False
    _, name, alts, *_ = entry
    for ans in [name] + alts:
        norm_ans = _normalize(ans)
        # Exact normalized match
        if norm_guess == norm_ans:
            return True
        # Fuzzy match (>=85% similarity on normalized strings)
        if len(norm_guess) >= 5 and _fuzzy_ratio(norm_guess, norm_ans) >= 0.85:
            return True
        # Last name only match
        parts = ans.split()
        if len(parts) > 1:
            last = _normalize(parts[-1])
            if norm_guess == last and len(last) >= 4:
                return True
    return False


def _calc_points(clues_at_solve: int) -> int:
    if clues_at_solve <= 1:
        return 5
    elif clues_at_solve == 2:
        return 4
    elif clues_at_solve == 3:
        return 3
    elif clues_at_solve == 4:
        return 2
    else:
        return 1


def _pick_player(used_ids: set[int]) -> tuple:
    pool = [e for e in NBA_PLAYERS_DATA if e[0] not in used_ids]
    if not pool:
        used_ids.clear()
        pool = list(NBA_PLAYERS_DATA)
    choice = random.choice(pool)
    used_ids.add(choice[0])
    return choice


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class NbaGuessPlayer:
    user_id: int
    display_name: str
    score: int = 0
    answer: str | None = None
    answer_time: float | None = None


@dataclass
class NbaGuessTable:
    channel_id: int
    host_id: int
    host_name: str
    phase: str = "betting"  # betting | playing | between_rounds | closed
    players: dict[int, NbaGuessPlayer] = field(default_factory=dict)
    message: discord.Message | None = None
    round_num: int = 0
    total_rounds: int = DEFAULT_ROUNDS
    current_entry: tuple | None = None
    clues_revealed: int = 0
    round_start_time: float = 0.0
    round_winner: int | None = None
    round_points: int = 0
    race_task: asyncio.Task | None = field(default=None, repr=False)
    thread: discord.Thread | None = field(default=None, repr=False)
    round_solved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    used_ids: set[int] = field(default_factory=set)
    round_messages: list[discord.Message] = field(default_factory=list)
    stop_requested: bool = False


# ── Embeds ───────────────────────────────────────────────────────────────────


def _scoreboard(table: NbaGuessTable) -> str:
    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )
    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        prefix = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{prefix} **{p.display_name}** \u2014 {p.score} pts")
    return "\n".join(lines) or "*No players*"


def _betting_embed(table: NbaGuessTable) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001f3c0 NBA Player Guess",
        description=(
            f"**Rounds:** {table.total_rounds}\n"
            "Guess the NBA player from their career teams!\n"
            "Teams are revealed one at a time \u2014 fewer clues = more points."
        ),
        colour=discord.Colour.orange(),
    )
    if table.players:
        lines = [
            f"\U0001f3c0 **{p.display_name}**"
            + (f" ({p.score} pts)" if p.score > 0 else "")
            for p in table.players.values()
        ]
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet \u2014 click Join!*", inline=False)
    embed.add_field(
        name="Scoring",
        value="1 team = 5 pts \u2502 2 = 4 pts \u2502 3 = 3 pts \u2502 4 = 2 pts \u2502 5+ = 1 pt",
        inline=False,
    )
    embed.set_footer(text=f"Host: {table.host_name} \u2503 Min {MIN_PLAYERS} players")
    return embed


def _total_clue_stages(entry: tuple) -> int:
    """Total clue stages: N teams + 1 (seasons) + 1 (stats)."""
    _, _, _, stints, *_ = entry
    return len(stints) + 2


def _team_clues_text(table: NbaGuessTable) -> str:
    _, _, _, stints, *_ = table.current_entry
    num_teams = len(stints)
    show_seasons = table.clues_revealed > num_teams  # stage N+1
    lines: list[str] = []
    for i, stint in enumerate(stints, 1):
        team, start, end = stint
        if i <= table.clues_revealed:
            if show_seasons:
                lines.append(f"{i}. {team} ({start}\u2013{end})")
            else:
                lines.append(f"{i}. {team}")
        else:
            lines.append(f"{i}. ???")
    return "\n".join(lines)


def _stats_text(entry: tuple) -> str:
    """Format career stats for display."""
    *_, stats = entry
    if not stats:
        return ""
    return (
        f"**{stats['ppg']}** ppg \u2502 "
        f"**{stats['rpg']}** rpg \u2502 "
        f"**{stats['apg']}** apg \u2502 "
        f"{stats['gp']} GP"
    )


def _full_career_text(entry: tuple) -> str:
    """Full career path with seasons for result embeds."""
    _, _, _, stints, *_ = entry
    parts = [f"{team} ({start}\u2013{end})" for team, start, end in stints]
    return " \u2192 ".join(parts)


def _playing_embed(table: NbaGuessTable, remaining: int | None = None) -> discord.Embed:
    entry = table.current_entry
    _, _, _, stints, *_ = entry
    num_teams = len(stints)
    total_stages = _total_clue_stages(entry)
    show_stats = table.clues_revealed > num_teams + 1  # stage N+2

    embed = discord.Embed(
        title=f"\U0001f3c0 NBA Player Guess \u2014 Round {table.round_num}/{table.total_rounds}",
        colour=discord.Colour.dark_orange(),
    )
    desc = f"**Career Path:**\n{_team_clues_text(table)}"
    if show_stats:
        desc += f"\n\n\U0001f4ca **Career Stats:** {_stats_text(entry)}"
    desc += "\n\n**Type the player name in chat!**"
    embed.description = desc

    secs = remaining if remaining is not None else ROUND_TIME
    embed.add_field(name="\u23f1\ufe0f Time", value=f"**{secs}s**", inline=True)

    if table.clues_revealed < num_teams:
        hint_status = f"Next team in {CLUE_INTERVAL}s"
    elif table.clues_revealed == num_teams:
        hint_status = f"Seasons in {CLUE_INTERVAL}s"
    elif table.clues_revealed == num_teams + 1:
        hint_status = f"Stats in {CLUE_INTERVAL}s"
    else:
        hint_status = "All clues revealed!"
    embed.add_field(
        name=f"Clues: {min(table.clues_revealed, total_stages)}/{total_stages}",
        value=hint_status,
        inline=True,
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


def _round_result_embed(table: NbaGuessTable) -> discord.Embed:
    winner = table.players[table.round_winner]
    entry = table.current_entry
    _, name, *_ = entry
    solve_time = winner.answer_time - table.round_start_time
    is_last = table.round_num >= table.total_rounds

    embed = discord.Embed(
        title=f"\U0001f3c0 Round {table.round_num} \u2705",
        colour=discord.Colour.green(),
    )
    embed.description = (
        f"\U0001f3c6 **{winner.display_name}** got it in **{solve_time:.1f}s** "
        f"for **{table.round_points} pts**!\n\n"
        f"It's **{name}**!\n{_full_career_text(entry)}\n"
        f"\U0001f4ca {_stats_text(entry)}"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _timeout_embed(table: NbaGuessTable) -> discord.Embed:
    entry = table.current_entry
    _, name, *_ = entry
    is_last = table.round_num >= table.total_rounds

    embed = discord.Embed(
        title=f"\U0001f3c0 Round {table.round_num} (Time's Up!)",
        colour=discord.Colour.dark_grey(),
    )
    embed.description = (
        f"Nobody got it in {ROUND_TIME} seconds!\n\n"
        f"It was **{name}**!\n{_full_career_text(entry)}\n"
        f"\U0001f4ca {_stats_text(entry)}"
    )
    embed.add_field(name="Scoreboard", value=_scoreboard(table), inline=False)
    if not is_last:
        embed.set_footer(text="Next round in a few seconds\u2026")
    else:
        embed.set_footer(text="Final round \u2014 calculating results\u2026")
    return embed


def _final_embed(
    table: NbaGuessTable,
    elo_changes: dict[int, tuple[float, float]] | None = None,
) -> discord.Embed:
    max_score = max((p.score for p in table.players.values()), default=0)
    no_winner = max_score == 0

    embed = discord.Embed(
        title="\U0001f3c0 NBA Player Guess \u2014 Results",
        colour=discord.Colour.gold() if not no_winner else discord.Colour.dark_grey(),
    )

    sorted_players = sorted(
        table.players.values(), key=lambda p: p.score, reverse=True,
    )

    if no_winner:
        embed.description = "No rounds were won \u2014 game over!"
    else:
        winner = sorted_players[0]
        embed.description = (
            f"\U0001f3c6 **{winner.display_name}** wins with "
            f"**{winner.score}** point{'s' if winner.score != 1 else ''}!"
        )

    lines: list[str] = []
    for i, p in enumerate(sorted_players):
        medal = MEDALS[i] if i < len(MEDALS) and p.score > 0 else "\u25aa\ufe0f"
        lines.append(f"{medal} **{p.display_name}** \u2014 {p.score} pts")
    embed.add_field(name="Results", value="\n".join(lines), inline=False)
    embed.add_field(name="Rounds Played", value=str(table.round_num), inline=True)

    if elo_changes:
        elo_lines: list[str] = []
        for p in sorted_players:
            if p.user_id in elo_changes:
                old, new = elo_changes[p.user_id]
                elo_lines.append(f"**{p.display_name}**: {fmt_elo_change(old, new)}")
        if elo_lines:
            embed.add_field(name="\U0001f4c8 ELO", value="\n".join(elo_lines), inline=False)

    embed.set_footer(text=f"Host: {table.host_name}")
    return embed


# ── Rounds selector options ──────────────────────────────────────────────────

_ROUNDS_OPTIONS = [
    discord.SelectOption(label="5 Rounds", value="5", emoji="\u0035\ufe0f\u20e3"),
    discord.SelectOption(label="10 Rounds", value="10", emoji="\U0001f51f", default=True),
    discord.SelectOption(label="15 Rounds", value="15", emoji="\U0001f4af"),
    discord.SelectOption(label="20 Rounds", value="20", emoji="\U0001f525"),
]


# ── View ─────────────────────────────────────────────────────────────────────


class NbaEndGameView(ui.View):
    """Button posted in the thread so any player can stop the game early."""

    def __init__(self, table: NbaGuessTable) -> None:
        super().__init__(timeout=None)
        self.table = table

    @ui.button(
        label="End Game", style=discord.ButtonStyle.danger,
        emoji="\u23f9\ufe0f", row=0,
    )
    async def end_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if self.table.phase == "closed":
            await interaction.response.send_message(
                "The game has already ended.", ephemeral=True,
            )
            return
        if (
            interaction.user.id != self.table.host_id
            and interaction.user.id not in self.table.players
        ):
            await interaction.response.send_message(
                "Only players can end the game!", ephemeral=True,
            )
            return
        if self.table.stop_requested:
            await interaction.response.send_message(
                "Already ending\u2026", ephemeral=True,
            )
            return
        self.table.stop_requested = True
        self.table.round_solved.set()  # wake up the game loop immediately
        button.disabled = True
        button.label = "Ending\u2026"
        await interaction.response.edit_message(view=self)


class NbaGuessView(ui.View):
    def __init__(
        self, table: NbaGuessTable, active_tables: dict[int, NbaGuessTable],
    ) -> None:
        super().__init__(timeout=900)
        self.table = table
        self.active_tables = active_tables
        self._update_buttons()

    def _update_buttons(self) -> None:
        phase = self.table.phase
        betting = phase == "betting"
        racing = phase in ("playing", "between_rounds")

        self.start_btn.disabled = not betting or len(self.table.players) < MIN_PLAYERS
        self.join_btn.disabled = not betting
        self.leave_btn.disabled = not betting
        self.close_btn.disabled = racing
        self.rounds_select.disabled = not betting

    # ── Buttons ──────────────────────────────────────────────────────────

    @ui.button(
        label="Start", style=discord.ButtonStyle.success, emoji="\u25b6\ufe0f", row=0,
    )
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start!", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Already started!", ephemeral=True)
            return
        if len(self.table.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Need at least {MIN_PLAYERS} player(s)!", ephemeral=True,
            )
            return
        await self._start_race(interaction)

    @ui.button(
        label="Join", style=discord.ButtonStyle.primary, emoji="\U0001f3c0", row=0,
    )
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Game in progress! Wait for the next one.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        if uid in self.table.players:
            await interaction.response.send_message("You're already in!", ephemeral=True)
            return
        if len(self.table.players) >= MAX_PLAYERS:
            await interaction.response.send_message("Table is full!", ephemeral=True)
            return
        self.table.players[uid] = NbaGuessPlayer(
            user_id=uid,
            display_name=interaction.user.display_name,
        )
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, emoji="\U0001f6aa", row=0,
    )
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid = interaction.user.id
        if uid not in self.table.players:
            await interaction.response.send_message("You're not at this table.", ephemeral=True)
            return
        if self.table.phase != "betting":
            await interaction.response.send_message("Can't leave during a game!", ephemeral=True)
            return
        del self.table.players[uid]
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    @ui.button(
        label="Close Table", style=discord.ButtonStyle.danger, emoji="\u2716\ufe0f", row=1,
    )
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can close the table!", ephemeral=True,
            )
            return
        if self.table.phase in ("playing", "between_rounds"):
            await interaction.response.send_message(
                "Can't close during a game! Wait for it to finish.", ephemeral=True,
            )
            return
        await self._close_table(interaction)

    # ── Rounds selector ──────────────────────────────────────────────────

    @ui.select(
        placeholder="Rounds: 10",
        options=_ROUNDS_OPTIONS,
        row=2,
    )
    async def rounds_select(
        self, interaction: discord.Interaction, select: ui.Select,
    ) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message(
                "Only the host can change the rounds!", ephemeral=True,
            )
            return
        if self.table.phase != "betting":
            await interaction.response.send_message(
                "Can't change rounds during a game!", ephemeral=True,
            )
            return
        val = int(select.values[0])
        self.table.total_rounds = val
        select.placeholder = f"Rounds: {val}"
        for opt in select.options:
            opt.default = opt.value == str(val)
        await interaction.response.edit_message(
            embed=_betting_embed(self.table), view=self,
        )

    # ── Game logic ───────────────────────────────────────────────────────

    async def _start_race(self, interaction: discord.Interaction) -> None:
        table = self.table

        entry = _pick_player(table.used_ids)
        table.current_entry = entry
        table.clues_revealed = 1
        table.round_num = 1
        table.round_winner = None
        table.round_points = 0
        table.round_solved.clear()
        table.round_messages.clear()
        table.phase = "playing"
        table.round_start_time = time.monotonic()

        for p in table.players.values():
            p.answer = None
            p.answer_time = None

        self._update_buttons()
        in_progress = discord.Embed(
            title="\U0001f3c0 NBA Player Guess \u2014 In Progress",
            description="Game running in the thread below! Type your answers there.",
            colour=discord.Colour.orange(),
        )
        players_text = ", ".join(p.display_name for p in table.players.values())
        in_progress.add_field(name="Players", value=players_text or "\u2014", inline=False)
        await interaction.response.edit_message(embed=in_progress, view=self)

        msg = await interaction.original_response()
        thread = await msg.create_thread(name=f"NBA Player Guess \u2014 {table.host_name}")
        table.thread = thread

        await thread.send(
            "\U0001f3c1 **NBA Player Guess started!** Type your answers here.",
            view=NbaEndGameView(table),
        )

        table.message = await thread.send(embed=_playing_embed(table))
        table.race_task = asyncio.create_task(self._race_loop())

    async def _wait_for_solve_or_timeout(self) -> bool:
        """Wait for someone to solve or timeout. Returns True if solved."""
        table = self.table
        deadline = table.round_start_time + ROUND_TIME
        total_stages = _total_clue_stages(table.current_entry)

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return table.round_winner is not None

            next_events: list[float] = []
            # Next clue reveal (teams, then seasons, then stats)
            if table.clues_revealed < total_stages:
                next_clue_at = table.round_start_time + (table.clues_revealed * CLUE_INTERVAL)
                time_to_clue = next_clue_at - now
                if time_to_clue > 0:
                    next_events.append(time_to_clue)
                else:
                    next_events.append(0.1)
            # Timer tick every 5 seconds
            next_events.append(min(5.0, remaining))
            wait = min(next_events)

            try:
                await asyncio.wait_for(table.round_solved.wait(), timeout=wait)
                return True
            except asyncio.TimeoutError:
                if table.round_winner is not None:
                    return True

                now2 = time.monotonic()
                # Reveal next clue stage
                if table.clues_revealed < total_stages:
                    next_clue_at = table.round_start_time + (table.clues_revealed * CLUE_INTERVAL)
                    if now2 >= next_clue_at:
                        table.clues_revealed += 1

                secs_left = max(0, int(deadline - now2))
                if secs_left > 0 and table.message:
                    try:
                        await table.message.edit(
                            embed=_playing_embed(table, remaining=secs_left),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

    async def _race_loop(self) -> None:
        table = self.table
        try:
            rnd = 0
            consecutive_unanswered = 0
            while True:
                rnd += 1

                if rnd > 1:
                    entry = _pick_player(table.used_ids)
                    table.current_entry = entry
                    table.clues_revealed = 1
                    table.round_num = rnd
                    table.round_winner = None
                    table.round_points = 0
                    table.round_solved.clear()
                    table.round_messages.clear()
                    table.phase = "playing"
                    table.round_start_time = time.monotonic()

                    for p in table.players.values():
                        p.answer = None
                        p.answer_time = None

                    self._update_buttons()
                    if table.thread:
                        try:
                            table.message = await table.thread.send(
                                embed=_playing_embed(table),
                            )
                        except discord.HTTPException:
                            pass

                solved = await self._wait_for_solve_or_timeout()

                if table.stop_requested:
                    break

                if solved and table.round_winner is not None:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_round_result_embed(table),
                            )
                        except discord.HTTPException:
                            pass
                else:
                    if table.message:
                        try:
                            await table.message.edit(
                                embed=_timeout_embed(table),
                            )
                        except discord.HTTPException:
                            pass

                # Inactivity: end if nobody answered N rounds in a row
                if table.round_winner is None:
                    consecutive_unanswered += 1
                else:
                    consecutive_unanswered = 0
                if consecutive_unanswered >= INACTIVITY_ROUNDS:
                    if table.thread:
                        try:
                            await table.thread.send(
                                "\u23f8\ufe0f No one answered for 5 consecutive rounds — ending due to inactivity."
                            )
                        except discord.HTTPException:
                            pass
                    break

                if rnd >= table.total_rounds:
                    break

                await self._clear_round_messages()

                table.phase = "between_rounds"
                await asyncio.sleep(ROUND_DELAY)

                if table.stop_requested:
                    break

            await self._clear_round_messages()
            if table.stop_requested and table.thread:
                try:
                    await table.thread.send("\u23f9\ufe0f Game ended early.")
                except discord.HTTPException:
                    pass
            await self._end_game()

        except asyncio.CancelledError:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    log.exception("Unhandled error in nbaguess.py")
        except Exception:
            table.phase = "closed"
            self.active_tables.pop(table.channel_id, None)
            if table.thread:
                try:
                    await table.thread.edit(archived=True)
                except Exception:
                    log.exception("Unhandled error in nbaguess.py")

    async def _clear_round_messages(self) -> None:
        messages = list(self.table.round_messages)
        self.table.round_messages.clear()
        for msg in messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

    async def _end_game(self) -> None:
        table = self.table
        table.phase = "closed"

        # ELO update — rank by score (highest = 1st)
        elo_changes: dict[int, tuple[float, float]] = {}
        max_score = max((p.score for p in table.players.values()), default=0)
        if max_score > 0 and len(table.players) >= 2:
            sorted_players = sorted(
                table.players.values(),
                key=lambda p: p.score,
                reverse=True,
            )
            finish_order = [p.user_id for p in sorted_players]
            try:
                elo_changes = await update_elo_multiplayer(finish_order, "nbaguess", "nbaguess", scores={p.user_id: p.score for p in sorted_players})
            except Exception:
                log.exception("Unhandled error in nbaguess.py")

        embed = _final_embed(table, elo_changes)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                await table.message.edit(embed=embed)
            except discord.HTTPException:
                pass

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except discord.HTTPException:
                pass

    async def _close_table(self, interaction: discord.Interaction) -> None:
        table = self.table

        if table.round_num == 0:
            embed = discord.Embed(
                title="\U0001f3c0 NBA Player Guess \u2014 Closed",
                description="Table closed.",
                colour=discord.Colour.dark_grey(),
            )
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
            self.stop()
            self.active_tables.pop(table.channel_id, None)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        table.phase = "closed"
        embed = _final_embed(table)

        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        self.stop()
        self.active_tables.pop(table.channel_id, None)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        table = self.table

        if table.race_task and not table.race_task.done():
            table.race_task.cancel()

        if table.phase == "closed":
            return

        await self._clear_round_messages()
        table.phase = "closed"
        self.active_tables.pop(table.channel_id, None)

        if table.message:
            try:
                embed = discord.Embed(
                    title="\U0001f3c0 NBA Player Guess \u2014 Timed Out",
                    description="Table timed out.",
                    colour=discord.Colour.dark_grey(),
                )
                await table.message.edit(embed=embed, view=None)
            except Exception:
                log.exception("Unhandled error in nbaguess.py")

        if table.thread:
            try:
                await table.thread.edit(archived=True)
            except Exception:
                log.exception("Unhandled error in nbaguess.py")


# ── Cog ──────────────────────────────────────────────────────────────────────


class NbaGuessCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_tables: dict[int, NbaGuessTable] = {}

    async def nba(self, interaction: discord.Interaction, rounds: int = DEFAULT_ROUNDS) -> None:
        channel_id = interaction.channel_id
        if channel_id in self.active_tables:
            existing = self.active_tables[channel_id]
            _has_running = any(
                (t := getattr(existing, n, None)) is not None and not t.done()
                for n in ("game_task", "race_task", "sim_task", "round_task", "_round_task", "trade_task", "fly_task", "_shot_clock_task", "_countdown_task")
            )
            if _has_running:
                await interaction.response.send_message(
                    "There's already an NBA guess game in this channel!",
                    ephemeral=True,
                )
                return
            del self.active_tables[channel_id]

        rounds = max(1, min(rounds, MAX_ROUNDS_CAP))

        table = NbaGuessTable(
            channel_id=channel_id,
            host_id=interaction.user.id,
            host_name=interaction.user.display_name,
            total_rounds=rounds,
        )
        view = NbaGuessView(table, self.active_tables)
        embed = _betting_embed(table)
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.NotFound:
            return  # interaction expired — don't leave a ghost table
        self.active_tables[channel_id] = table
        table.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        uid = message.author.id
        guess = message.content.strip()

        table = None
        for t in self.active_tables.values():
            if t.thread is not None and t.thread.id == message.channel.id:
                table = t
                break
        if table is None or table.phase != "playing":
            return
        if uid not in table.players or table.round_winner is not None:
            return
        if len(guess) < 3:
            return
        alpha_chars = sum(1 for c in guess if c.isalpha())
        if alpha_chars < len(guess) * 0.5:
            return

        table.round_messages.append(message)

        if check_nba_answer(guess, table.current_entry):
            now = time.monotonic()
            player = table.players[uid]
            points = _calc_points(table.clues_revealed)
            player.answer = guess
            player.answer_time = now
            player.score += points
            table.round_winner = uid
            table.round_points = points
            try:
                await message.add_reaction("\u2705")
            except discord.HTTPException:
                pass
            table.round_solved.set()
        else:
            try:
                await message.add_reaction("\u274c")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NbaGuessCog(bot))
