import os

# Arrow's default allocator is mimalloc (pyarrow 25). Inside the PyInstaller-frozen desktop sidecar it
# segfaults (NULL dereference in mi_thread_init, libarrow) the first time a worker thread allocates
# through pyarrow, which killed every chat turn that reached the semantic cache (lancedb). Use the system
# allocator instead. This must be set before pyarrow's first allocation, so it lives at package import.
# setdefault: an operator who exports ARROW_DEFAULT_MEMORY_POOL keeps control.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

__version__ = "1.0.1"
