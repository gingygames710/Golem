"""The one place Golem's version number lives.

agent.py, builder.py and agent_gui.py all import __version__ from here, and the auto-update
checker compares it against the latest GitHub release. When cutting a release, bump this AND the
matching MyAppVersion in golem.iss (two spots, on purpose - the installer compiler can't import Python).
"""

__version__ = "1.2.0"

# The public GitHub repo the update checker queries for new releases.
GITHUB_REPO = "gingygames710/Golem"
