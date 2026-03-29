import requests
url = "https://sdk.companywebcast.com/vods3/_definst_/mp4:amazons3/clientdataprivate-eu-bv/gemeenterotterdam/webcasts/20260203_7/mp4/bb_nl.mp4/playlist.m3u8?Signature=ZxPqDqyhGhc5hdbtk3iZtZLK6iMoeyu3xQYVJAZsMkNShnhxdf3dz94cNcXPcpUG-qd~m0LalGm584tx9fuckDj6eFPj0xRDuvC-OzveedQa5fMjcUxsRplzJBNeD48YQgdR8rWgSDCmpAxXmLi5XjHauzC0XZG9lb8X6ZZLL049b4ZPYptU3hGmnKKNjOg7X7lm90YIjAVhbCyTyptKsJmsIVbLwCrDXs4gyootbaU1483Lu19a0Ri9HNSn-R10Ip3j-2JzQri2r34lJNsdgrV6YWPfmGRtgnL-I-1ONt3YpEHzJ~2214zJYKQvPdazgM9dDs3xUoExu2vIHyRxwA__&Policy=eyJTdGF0ZW1lbnQiOiBbeyJSZXNvdXJjZSI6Imh0dHBzOi8vc2RrLmNvbXBhbnl3ZWJjYXN0LmNvbS9wbGF5ZXJzLzQ2MmFjZjQ3LWZkNGEtNDY5MS1iNjNkLTNjZmJiY2JmYjBmZi8qIiwiQ29uZGl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJBV1M6RXBvY2hUaW1lIjoxNzkxNDQ3NDQ5fSwiSXBBZGRyZXNzIjp7IkFXUzpTb3VyY2VJcCI6IjAuMC4wLjAvMCJ9fX1dfQ__&Key-Pair-Id=APKAIWM6LAZJX3UVVARQ"
headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"
}
resp = requests.get(url, headers=headers)
print("Status:", resp.status_code)
print("Text:", resp.text)
if resp.status_code != 200:
    # Try different combinations of tokens
    pass
