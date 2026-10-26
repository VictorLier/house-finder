import unittest

from house_finder.extract import _poi_type


class PoiTypeTests(unittest.TestCase):
    def test_grocery_shops(self):
        for shop in ("supermarket", "convenience", "grocery", "greengrocer", "deli"):
            with self.subTest(shop=shop):
                self.assertEqual(_poi_type({"shop": shop}), "grocery")

    def test_institution_categories(self):
        cases = {
            "school": {"amenity": "school"},
            "daycare": {"amenity": "kindergarten"},
            "childcare": {"amenity": "childcare"},
            "social day care": {"social_facility": "day_care"},
            "higher education": {"amenity": "university"},
            "healthcare": {"healthcare": "doctor"},
            "library": {"amenity": "library"},
        }
        expected = {
            "school": "institution_school",
            "daycare": "institution_daycare",
            "childcare": "institution_daycare",
            "social day care": "institution_daycare",
            "higher education": "institution_higher_education",
            "healthcare": "institution_healthcare",
            "library": "institution_library",
        }
        for case, tags in cases.items():
            with self.subTest(case=case):
                self.assertEqual(_poi_type(tags), expected[case])

    def test_unrelated_features_are_ignored(self):
        self.assertIsNone(_poi_type({"shop": "clothes"}))
        self.assertIsNone(_poi_type({"amenity": "bench"}))
        self.assertIsNone(_poi_type({"healthcare": "no"}))


if __name__ == "__main__":
    unittest.main()
