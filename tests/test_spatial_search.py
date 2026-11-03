import unittest

from sragent_spatial.spatial_search import (
    PLATFORM_KEYS,
    SPATIAL_PLATFORMS,
    build_spatial_query,
    check_platforms,
    platform_kinds,
)


class SpatialSearchQueryTests(unittest.TestCase):
    def test_catalog_is_complete_and_typed(self):
        self.assertEqual(len(SPATIAL_PLATFORMS), 7)
        for key in PLATFORM_KEYS:
            entry = SPATIAL_PLATFORMS[key]
            self.assertTrue(entry["terms"])
            self.assertIn(entry["kind"], {"sequencing", "imaging"})

    def test_query_contains_platform_or_group_and_series_filter(self):
        query = build_spatial_query("Homo sapiens", "lung", ["Xenium", "Visium HD"])
        self.assertIn('"Homo sapiens"[Organism]', query)
        self.assertIn('"lung"[All Fields]', query)
        self.assertIn('"Xenium"[All Fields]', query)
        self.assertIn('"Visium HD"[All Fields]', query)
        self.assertIn("gse[ETYP]", query)
        self.assertIn(" OR ", query)

    def test_query_never_imposes_read_layout_or_sequencer(self):
        query = build_spatial_query("Mus musculus", "brain", ["MERFISH"])
        self.assertNotIn("Illumina", query)
        self.assertNotIn("paired", query)
        self.assertNotIn("10x", query.lower())  # MERFISH is not 10x

    def test_invalid_inputs_are_rejected(self):
        for bad in ('human" OR mouse', "", "lung\nbrain"):
            with self.assertRaises(ValueError):
                build_spatial_query(bad, "lung", ["Visium"])
        with self.assertRaises(ValueError):
            build_spatial_query("Homo sapiens", "lung", ["scRNA-seq"])
        with self.assertRaises(ValueError):
            check_platforms([])

    def test_platform_kinds_classify_imaging_vs_sequencing(self):
        kinds = platform_kinds(["Xenium", "MERFISH", "CosMx"])
        self.assertEqual(set(kinds.values()), {"imaging"})
        kinds = platform_kinds(["Visium", "Stereo-seq", "Slide-seq", "Visium HD"])
        self.assertEqual(set(kinds.values()), {"sequencing"})

    def test_duplicate_platform_keys_are_deduplicated(self):
        self.assertEqual(check_platforms(["Visium", "Visium"]), ["Visium"])


    def test_targeted_study_falls_back_when_series_keywords_miss(self):
        from unittest.mock import Mock, patch
        from sragent_spatial.spatial_search import spatial_search

        client = Mock()
        client.get_json.return_value = {"esearchresult": {"count": "0", "idlist": []}}
        record = {"source_accession": "GSE123"}
        with patch("sragent_spatial.geo.lookup", return_value=([record], {})) as lookup:
            records, evidence = spatial_search(client, "Homo sapiens", "lung", ["Visium HD"], study="GSE123")
        lookup.assert_called_once_with(client, "GSE123")
        self.assertEqual(records, [record])
        self.assertTrue(evidence["accession_lookup_fallback"])
        self.assertEqual(evidence["total_hits"], 0)


if __name__ == "__main__":
    unittest.main()
