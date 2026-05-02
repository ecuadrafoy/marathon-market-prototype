"""
Tests for runner_sim/zone_sim/extraction_ai.py

Focus: squad_doctrine derivation — the function that converts a list of shell
names into a Doctrine enum. This is the bridge between the shell economy and
actual zone behaviour, so getting the majority/tiebreak logic right matters.
"""

import pytest

from runner_sim.zone_sim.extraction_ai import Doctrine, squad_doctrine


class TestSquadDoctrine:
    def test_all_destroyer_is_greedy(self):
        assert squad_doctrine(["Destroyer", "Destroyer", "Destroyer"]) == Doctrine.GREEDY

    def test_all_thief_is_cautious(self):
        assert squad_doctrine(["Thief", "Thief", "Thief"]) == Doctrine.CAUTIOUS

    def test_all_triage_is_support(self):
        assert squad_doctrine(["Triage", "Triage", "Triage"]) == Doctrine.SUPPORT

    def test_all_vandal_is_balanced(self):
        assert squad_doctrine(["Vandal", "Vandal", "Vandal"]) == Doctrine.BALANCED

    def test_majority_wins(self):
        """Two Destroyers + one Thief → GREEDY (2 > 1)."""
        assert squad_doctrine(["Destroyer", "Destroyer", "Thief"]) == Doctrine.GREEDY

    def test_majority_wins_cautious(self):
        """Two Thieves + one Destroyer → CAUTIOUS."""
        assert squad_doctrine(["Thief", "Thief", "Destroyer"]) == Doctrine.CAUTIOUS

    def test_tiebreak_greedy_beats_cautious(self):
        """One Destroyer (GREEDY) + one Thief (CAUTIOUS) + one Vandal (BALANCED) → GREEDY wins tie."""
        result = squad_doctrine(["Destroyer", "Thief", "Vandal"])
        assert result == Doctrine.GREEDY

    def test_tiebreak_greedy_beats_support(self):
        """GREEDY beats SUPPORT in a tie."""
        assert squad_doctrine(["Destroyer", "Triage", "Vandal"]) == Doctrine.GREEDY

    def test_tiebreak_balanced_beats_cautious(self):
        """BALANCED beats CAUTIOUS when counts are tied and GREEDY is absent."""
        # Two BALANCED shells vs two CAUTIOUS shells — this needs a 4-runner squad
        # but we can test with 2 vs 2 using Vandal(BALANCED) + Rook(BALANCED) vs
        # Thief(CAUTIOUS) + Recon(CAUTIOUS). With 4 slots, counts are equal.
        # squad_doctrine has no squad-size restriction, so test directly.
        result = squad_doctrine(["Vandal", "Rook", "Thief", "Recon"])
        assert result == Doctrine.BALANCED

    def test_assassin_maps_to_greedy(self):
        """Assassin is a GREEDY shell."""
        assert squad_doctrine(["Assassin", "Assassin", "Assassin"]) == Doctrine.GREEDY

    def test_recon_maps_to_cautious(self):
        """Recon is a CAUTIOUS shell."""
        assert squad_doctrine(["Recon", "Recon", "Recon"]) == Doctrine.CAUTIOUS

    def test_rook_maps_to_balanced(self):
        """Rook is a BALANCED shell."""
        assert squad_doctrine(["Rook", "Rook", "Rook"]) == Doctrine.BALANCED
