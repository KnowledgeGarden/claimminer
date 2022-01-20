"""
Copyright Society Library and Conversence 2022-2023
"""
from typing import List, Dict, Optional
import re

from redis import Redis
import langchain
from langchain.cache import RedisCache
from langchain.llms import OpenAI
from langchain.schema import BaseOutputParser

langchain.llm_cache = RedisCache(Redis(db=6))


models = {
    "openai": [
      "text-davinci-003",
      "text-curie-001",
      "text-babbage-001",
      "text-ada-001"
    ]
}

DEFAULT_MODEL = "text-davinci-003"


def get_base_llm(model_name=DEFAULT_MODEL, temperature=0):
    return OpenAI(model_name=model_name, n=2, best_of=2, temperature=temperature)


class SinglePhraseParser(BaseOutputParser):
    """Class to parse the output into a simple dictionary with text."""

    @property
    def _type(self) -> str:
        """Return the type key."""
        return "single_phrase"

    def parse(self, text: str) -> Dict[str, str]:
        """Parse the output of an LLM call."""
        return [dict(text=text.strip())]


class BulletListParser(BaseOutputParser):
    """Class to parse the output into a list of dictionaries."""

    regex_pattern = re.compile(r"^\s*[-\+\*•]+\s+(.*)\s*$")

    @property
    def _type(self) -> str:
        """Return the type key."""
        return "bullet_list"

    def parse(self, text: str) -> List[Dict[str, str]]:
        """Parse the output of an LLM call."""
        lines = re.split(r"[\r\n]+", text)
        matches = [re.match(self.regex_pattern, line) for line in lines]
        matches = filter(None, matches)
        if not matches:
            raise ValueError("No answer")
        return [dict(text=r.group(1)) for r in matches]


class BulletListWithRefsParser(BaseOutputParser):
    """Class to parse the output into a list of dictionaries."""

    regex_pattern = re.compile(r"^\s*[-\+\*•]+\s+(.*)\s+\((\d+(,\s*\d+)*)\)\s*\.?\s*$")

    @property
    def _type(self) -> str:
        """Return the type key."""
        return "bullet_list_with_refs"

    def parse(self, text: str) -> List[Dict]:
        """Parse the output of an LLM call."""
        lines = re.split(r"[\r\n]+", text)
        matches = [re.match(self.regex_pattern, line) for line in lines]
        matches = filter(None, matches)
        if not matches:
            raise ValueError("No answer")
        return [dict(text=r.group(1), sources=[int(x) for x in r.group(2).split(",")]) for r in matches]


parsers = [SinglePhraseParser(), BulletListParser(), BulletListWithRefsParser()]

parsers_by_name = {
    p._type: p for p in parsers
}
