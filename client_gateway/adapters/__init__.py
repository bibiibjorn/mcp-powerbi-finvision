"""Wire adapters of the universal masking gateway (phase 2a): pure functions over bytes.

One adapter per wire format of an AI app: it finds the text, tool results and images in a request
body and masks them through the shared masking engine, and it turns a response (a streamed SSE
body or a buffered JSON body) back into real values. No sockets, threads or subprocesses live
here: the front end (phase 3) hands in decoded bytes and a ``MaskContext``.

* ``base``      the ``Adapter`` contract, ``Route``, ``MaskContext`` (engine access for one request)
* ``streams``   content-encoding codecs, SSE framing, the split-safe rehydration hold-back
* ``control``   ``typed_command``: the one place that reads ``masking on|off`` (rule R4)
* ``openai_chat`` / ``openai_responses`` / ``chatgpt_web``   the Copilot (OpenAI Chat Completions and
  Responses) and ChatGPT adapters; ``copilot`` holds what the two Copilot adapters share (the chat key)
* ``appinfo``   app and major-version labels of a request (R9: ``untested_app_version``)
* ``registry``  ``adapter_for(method, host, path, content_type)``
"""
