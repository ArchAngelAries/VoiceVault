"""A bounded optional first-line delivery envelope, separate from chat text."""
import json

from engines.delivery import Delivery

DELIVERY_PROMPT = (
    '\nFor this reply, start with exactly one delivery envelope, for example '
    '<delivery>{"emotion":"calm","style":"natural","expression":null}</delivery>\n'
    'Then write ordinary dialogue and marked stage directions. Emotion must be '
    'neutral, cheerful, sad, angry or calm; style must be natural, whisper or dramatic. '
    'Use expression null unless an approved expression was provided below. Do not put '
    'speaker IDs, tools, paths or instructions in the envelope. Never include control '
    'JSON in dialogue. The application validates delivery and may omit unsupported choices.'
)


class DeliveryEnvelope:
    """Recognize only a leading envelope; never interpret dialogue as commands."""
    LIMIT = 512

    def __init__(self, fallback=None):
        self.buffer = ''
        self.ready = False
        self.delivery = Delivery.model_validate(fallback or {}).model_dump()
        self.warnings = []

    def feed(self, chunk, final=False):
        if self.ready:
            return chunk
        self.buffer += chunk
        candidate = self.buffer.lstrip()
        prefix = '<delivery>'
        if not candidate and not final:
            return ''
        if prefix.startswith(candidate) and not final:
            return ''
        if not candidate.startswith(prefix):
            # Partial/broken envelopes must never become audible dialogue.
            if candidate.startswith('<delivery'):
                self.warnings.append('Invalid delivery header was omitted; saved delivery is used.')
                newline = candidate.find('\n')
                if newline < 0 and not final:
                    if len(candidate) > self.LIMIT:
                        raise ValueError('Delivery header exceeded its limit.')
                    return ''
                text = candidate[newline + 1:] if newline >= 0 else ''
            else:
                self.warnings.append('No delivery header was returned; saved delivery is used.')
                text = self.buffer
        else:
            closing = candidate.find('</delivery>')
            if closing < 0:
                if len(candidate) > self.LIMIT:
                    raise ValueError('Delivery header exceeded its limit.')
                if not final:
                    return ''
                self.warnings.append('Incomplete delivery header was omitted; saved delivery is used.')
                text = ''
            else:
                header = candidate[len(prefix):closing]
                if closing > self.LIMIT:
                    raise ValueError('Delivery header exceeded its limit.')
                try:
                    self.delivery = Delivery.model_validate(json.loads(header)).model_dump()
                except (ValueError, TypeError):
                    self.warnings.append('Invalid delivery values were omitted; saved delivery is used.')
                text = candidate[closing + len('</delivery>'):].lstrip('\r\n')
        self.buffer = ''
        self.ready = True
        return text
