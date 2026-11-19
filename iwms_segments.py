"""Owner-name rules for separately reported 2B activity."""
import pandas as pd


def special_business(warehouse, owners):
    if warehouse == "LAX2":
        # Percent signs supplied by the user express substring matching.
        mask = owners.astype("string").str.contains(
            r"Belk|Walmart|Kohl['’]s|JCP", case=False, regex=True, na=False
        )
        return "Trafilea 2B UPPH", mask
    if warehouse == "LAX5":
        return "Kreassive 2B UPPH", owners.eq("Kreassive, inc.").fillna(False)
    return None, pd.Series(False, index=owners.index)
