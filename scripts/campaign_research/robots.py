"""REP path matching with longest-match precedence, wildcards, and group merging.

Unlike urllib.robotparser, matching is independent of Allow/Disallow line order.
Crawl-delay and Request-rate are conservative supported extensions.
"""
import math
import re
import urllib.parse

UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def normalized_path(value, *, pattern=False):
    # Only rules interpret '*'/'$'; literal URI occurrences become %2A/%24.
    value = urllib.parse.quote(value, safe="/%:@!&'()+,;=?-._~[]" + ("*$" if pattern else ""))
    def percent(match):
        char = chr(int(match.group()[1:], 16))
        return char if char in UNRESERVED else match.group().upper()
    return re.sub(r"%[a-fA-F0-9]{2}", percent, value)


class RobotsPolicy:
    def __init__(self, text, user_agent):
        product = user_agent.split("/", 1)[0].lower()
        groups, agents, directives = [], [], []
        for line in text.splitlines():
            field, separator, value = line.split("#", 1)[0].partition(":")
            if not separator:
                continue
            field, value = field.strip().lstrip('\ufeff').lower(), value.strip()
            if field == "user-agent":
                if directives:
                    groups.append((agents, directives))
                    agents, directives = [], []
                if value:
                    agents.append(value.lower())
            elif agents and field in ("allow", "disallow", "crawl-delay", "request-rate"):
                directives.append((field, value))
        if agents:
            groups.append((agents, directives))
        matches = [values for group_agents, values in groups if product in group_agents]
        if not matches:
            matches = [values for group_agents, values in groups if "*" in group_agents]
        selected = [directive for values in matches for directive in values]
        self.rules, delays = [], []
        for field, value in selected:
            if field in ("allow", "disallow") and value:
                pattern = normalized_path(value, pattern=True)
                anchored = pattern.endswith("$")
                if anchored:
                    pattern = pattern[:-1]
                length = len(re.sub(r"%[A-F0-9]{2}", "X", pattern.replace("*", "")))
                self.rules.append((length, field == "allow", pattern.split("*"), anchored))
            elif field == "crawl-delay":
                try:
                    delay = float(value)
                    if math.isfinite(delay) and delay >= 0:
                        delays.append(delay)
                except ValueError:
                    pass
            elif field == "request-rate":
                rate = re.fullmatch(r"(\d+)\s*/\s*(\d+(?:\.\d+)?)", value)
                if rate and int(rate[1]) > 0:
                    delays.append(float(rate[2]) / int(rate[1]))
        self.delay = max(delays, default=0)

    def can_fetch(self, url):
        parsed = urllib.parse.urlsplit(url)
        path = normalized_path((parsed.path or "/") + ("?" + parsed.query if parsed.query else ""))
        matches = [(length, allow) for length, allow, parts, anchored in self.rules if self.matches(path, parts, anchored)]
        return max(matches, default=(0, True))[1]

    @staticmethod
    def matches(path, parts, anchored):
        # Literal substring searches avoid regex backtracking on hostile robots rules.
        if len(parts) == 1:
            return path == parts[0] if anchored else path.startswith(parts[0])
        if not path.startswith(parts[0]):
            return False
        position = len(parts[0])
        for part in parts[1:-1]:
            found = path.find(part, position)
            if found < 0:
                return False
            position = found + len(part)
        return (path.endswith(parts[-1]) and len(path) - len(parts[-1]) >= position) if anchored else path.find(parts[-1], position) >= 0
