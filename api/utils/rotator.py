class APIRotator:
    """
    Rotates through a list of API keys to ensure high availability and load balancing.
    """
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys if k.strip()]
        self.index = 0

    def get_next(self):
        if not self.keys:
            return None
        key = self.keys[self.index]
        self.index = (self.index + 1) % len(self.keys)
        return key

    def get_all(self):
        return self.keys
