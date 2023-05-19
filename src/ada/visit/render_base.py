# Using optional renderer pygfx
from abc import ABC, abstractmethod


class Renderer(ABC):
    @abstractmethod
    def render(self, obj):
        pass

    @abstractmethod
    def update(self):
        pass
