from .ashby import AshbyFetcher
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship
from .getro import GetroFetcher
from .github_lists import SimplifyFetcher, SpeedyApplyFetcher
from .greenhouse import GreenhouseFetcher
from .hackernews import HackerNewsFetcher
from .himalayas import HimalayasFetcher
from .internshala import InternshalaFetcher
from .lever import LeverFetcher
from .peerlist import PeerlistFetcher
from .remoteok import RemoteOKFetcher
from .remotive import RemotiveFetcher
from .unstop import UnstopFetcher
from .weworkremotely import WeWorkRemotelyFetcher
from .workable import WorkableFetcher
from .ycombinator import YCombinatorFetcher

FETCHERS: dict[str, Fetcher] = {
    f.name: f for f in (
        GreenhouseFetcher(), LeverFetcher(), AshbyFetcher(), WorkableFetcher(),
        RemotiveFetcher(), WeWorkRemotelyFetcher(), HackerNewsFetcher(), RemoteOKFetcher(), HimalayasFetcher(),
        InternshalaFetcher(), UnstopFetcher(), SimplifyFetcher(), SpeedyApplyFetcher(), YCombinatorFetcher(),
        GetroFetcher(), PeerlistFetcher(),
    )
}

__all__ = ["FETCHERS", "FetchOutcome", "Fetcher", "Job", "is_entry_level", "is_internship"]
