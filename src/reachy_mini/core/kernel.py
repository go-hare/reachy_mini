"""Thin naming bridge for the copied legacy core kernel."""

from .agent import BrainKernel, BrainKernel as AgentHarnessKernel

__all__ = ["AgentHarnessKernel", "BrainKernel"]
