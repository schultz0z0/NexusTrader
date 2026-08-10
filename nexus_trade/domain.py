from enum import Enum


class Lane(str, Enum):
    CHAMPION = "champion_baseline"
    TRIAL = "challenger_trial"


class CampaignStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CLOSED = "CLOSED"


class VersionStatus(str, Enum):
    CHAMPION = "CHAMPION"
    TRIAL = "TRIAL"
    SHADOW = "SHADOW"
    RETIRED = "RETIRED"
