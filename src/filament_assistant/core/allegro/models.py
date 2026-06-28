from dataclasses import dataclass, field


@dataclass
class OfferImage:
    url: str


@dataclass
class Price:
    amount: str
    currency: str


@dataclass
class Offer:
    id: str
    name: str
    url: str
    price: Price | None
    images: list[OfferImage] = field(default_factory=list)


@dataclass
class ListingPage:
    offers: list[Offer]
    total_count: int
    offset: int
    limit: int


@dataclass
class ParamValue:
    id: str
    name: str


@dataclass
class FilterParam:
    id: str
    name: str
    values: list[ParamValue]


@dataclass
class FilamentFilters:
    brands: list[ParamValue]
    types: list[ParamValue]
