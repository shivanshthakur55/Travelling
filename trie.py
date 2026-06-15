"""
trie.py

In-memory Trie for instant address prefix autocomplete.

The Trie is pre-populated at server startup from the SQLite location cache,
giving O(k) prefix lookups (k = query length) at zero external API cost.
New geocoding results are inserted at runtime so the Trie grows smarter
over time without requiring a restart.

Memory footprint
────────────────
Each TrieNode uses __slots__ to strip per-object __dict__ overhead.
Typical cost: ~200 bytes/node × ~15 nodes/word = ~3 KB per cached location.
1,000 locations ≈ 3 MB  |  100 locations ≈ 300 KB  (negligible)
"""


class TrieNode:
    """
    Single node in the Trie.
    __slots__ eliminates the per-instance __dict__, cutting memory by ~30%.
    """
    __slots__ = ("children", "results")

    def __init__(self):
        self.children: dict[str, "TrieNode"] = {}
        self.results:  list[dict] = []   # payloads stored when a word ends here


class LocationTrie:
    """
    Prefix Trie for location autocomplete suggestions.

    Example
    ───────
    trie = LocationTrie()
    trie.insert_location("shimla", 31.10, 77.17, "Shimla, HP, India")
    hits = trie.search("shi", limit=6)
    # → [{"display_name": "Shimla, HP, India", "short_name": "Shimla, HP",
    #      "lat": 31.10, "lon": 77.17}]
    """

    def __init__(self):
        self.root   = TrieNode()
        self._count = 0   # total (word → payload) pairs inserted

    # ─────────────────────────────────────────────
    # Public: insert
    # ─────────────────────────────────────────────

    def insert(self, word: str, payload: dict) -> None:
        """
        Walk the Trie character-by-character and store `payload` at the
        terminal node of `word` (lowercased).  Duplicate payloads (same
        display_name) at the same node are silently ignored.
        """
        if not word or not word.strip():
            return
        node = self.root
        for ch in word.lower().strip():
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]

        # Deduplicate by display_name to avoid bloating the result list
        display = payload.get("display_name", "")
        if not any(r.get("display_name") == display for r in node.results):
            node.results.append(payload)
            self._count += 1

    def insert_location(self, place_name: str,
                        lat: float, lon: float, address: str) -> None:
        """
        Convenience wrapper — builds the standard payload dict and indexes
        the location under two keys:
          1. The raw `place_name` (exactly as the user typed the search)
          2. The first comma-separated token of `address` (the city/area name)
             — so "Shimla" is still found even if the query was "shimla hp"
        """
        payload = {
            "display_name": address,
            "short_name":   _make_short_label(address),
            "lat":          lat,
            "lon":          lon,
        }
        self.insert(place_name, payload)

        # Also index by the first meaningful word in the formatted address
        first_token = address.split(",")[0].strip()
        if first_token.lower() != place_name.lower():
            self.insert(first_token, payload)

    # ─────────────────────────────────────────────
    # Public: search
    # ─────────────────────────────────────────────

    def search(self, prefix: str, limit: int = 6) -> list[dict]:
        """
        Return up to `limit` location payloads whose stored word starts
        with `prefix` (case-insensitive).

        Complexity: O(|prefix| + nodes_visited)
        Returns []  if no prefix match exists.
        """
        node = self.root
        for ch in prefix.lower().strip():
            if ch not in node.children:
                return []
            node = node.children[ch]

        results: list[dict] = []
        self._collect(node, results, limit)
        return results

    # ─────────────────────────────────────────────
    # Private: DFS collector
    # ─────────────────────────────────────────────

    def _collect(self, node: TrieNode, results: list, limit: int) -> None:
        """DFS from `node`, appending payloads until `limit` is reached."""
        if len(results) >= limit:
            return
        if node.results:
            remaining = limit - len(results)
            results.extend(node.results[:remaining])
        for child in node.children.values():
            if len(results) >= limit:
                return
            self._collect(child, results, limit)

    # ─────────────────────────────────────────────
    # Info
    # ─────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Total number of (word → payload) pairs currently stored."""
        return self._count

    def __repr__(self) -> str:
        return f"LocationTrie(entries={self._count})"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_short_label(address: str) -> str:
    """
    Derive a compact human-readable label from a full address string.

    "Shimla, Shimla (urban), Himachal Pradesh, 171001, India"
    → "Shimla, Himachal Pradesh"
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    # Skip purely numeric parts (PIN codes)
    meaningful = [p for p in parts if not p.isdigit()]
    if len(meaningful) >= 2:
        return f"{meaningful[0]}, {meaningful[1]}"
    return meaningful[0] if meaningful else address
