"""ESPN fantasy (fba) id maps."""

PRO_TEAM = {
    1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GSW", 10: "HOU",
    11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI",
    21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA",
}

POSITION = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
CENTER_POSITION_ID = 5

# lineupSlotId
SLOT = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C",
        11: "UTIL", 12: "BE", 13: "IR"}
BENCH, IR = 12, 13
# order the active slots are shown in
SLOT_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
# eligibleSlots ids that are positions (used to print "PG/SG")
POSITION_SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}

# ESPN stat ids inside stats[].stats
STAT = {"PTS": "0", "BLK": "1", "STL": "2", "AST": "3", "REB": "6", "TO": "11", "FGM": "13", "FGA": "14",
        "FTM": "15", "FTA": "16", "3PM": "17", "MIN": "40", "GS": "41", "GP": "42"}

# stats[].statSplitTypeId
SPLIT_SEASON, SPLIT_LAST7, SPLIT_LAST15, SPLIT_LAST30, SPLIT_GAME = 0, 1, 2, 3, 5
SOURCE_REAL, SOURCE_PROJ = 0, 1

INJURY_OUT = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
INJURY_DTD = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL", "PROBABLE"}
