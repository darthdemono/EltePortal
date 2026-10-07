"""An offline provider installed by a separate package."""


class ExampleProvider:
    def run(self, action: str, **params: str) -> dict[str, str]:
        if action != "ping":
            raise ValueError("unknown action")
        return {"reply": "pong", "institution": params.get("institution", "example")}
