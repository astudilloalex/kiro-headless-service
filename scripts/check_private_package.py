"""Prevent an opt-in provider-image push to an existing public/internal GHCR package."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

owner = os.environ["OWNER"]
package = os.environ["REPOSITORY"].lower() + "-kiro"
scope = "orgs" if os.environ["OWNER_TYPE"] == "Organization" else "users"
url = (
    f"https://api.github.com/{scope}/{urllib.parse.quote(owner)}/packages/container/"
    + urllib.parse.quote(package, safe="")
)
request = urllib.request.Request(
    url,
    headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    },
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise SystemExit("Cannot verify target package visibility; publication stopped") from None
    print("New GHCR package: it must retain GHCR's initial private visibility")
else:
    if data.get("visibility") != "private":
        raise SystemExit("Provider image publication requires a private GHCR package")
    print("Existing target package is private")
