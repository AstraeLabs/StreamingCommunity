# 29.07.25
# ruff: noqa: E402


from VibraVid.core.drm.manager import DRMManager

drm = DRMManager()

results = drm.add_keys(
    keys=[],
    license_url="",
    pssh="",
    kid_to_label={},
)

print(results)
