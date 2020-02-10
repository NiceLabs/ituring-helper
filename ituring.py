#!/usr/bin/env python3
import csv
import json
import os
import sys
from argparse import ArgumentParser
from datetime import datetime
from subprocess import list2cmdline
from typing import Dict, Tuple
from urllib.request import urljoin

import requests

session = requests.session()
session.headers["Content-Type"] = "application/json"

prefix = "https://api.ituring.com.cn/api/"
prefix_mainly = "http://www.ituring.com.cn/api/"
ebook_link = "http://www.ituring.com.cn/file/ebook/%s?type=%s"
token_path = "ituring-access-token.json"


def expand_paging(query):
    index = 1
    while True:
        payload = query(index)
        for item in payload["bookItems"]:
            yield item
        if payload["pagination"]["isLastPage"]:
            break
        index += 1


def get_book_shelf():
    def query(page):
        link = urljoin(prefix, "User/ShelfEBook")
        response = session.get(link, params={"page": page, "desc": True})
        return response.json()

    return expand_paging(query)


def get_favourite():
    def query(page):
        link = urljoin(prefix, "User/Fav/Books")
        response = session.get(link, params={"page": page})
        return response.json()

    return expand_paging(query)


def get_book(book_id):
    link = urljoin(prefix, "Book/%s" % book_id)
    response = session.get(link)
    if response.status_code == 404:
        return None
    return response.json()


def download_book(book_id: int):
    payload = get_book(book_id)

    def make_link(kind: str) -> Tuple[str, str]:
        link = ebook_link % (payload["encrypt"], kind)
        filename = "[{id:05}] {name}.{kind}".format(
            id=book_id, name=payload["name"].strip().replace("/", ""), kind=kind.lower()
        )
        return book_id, link, filename

    if payload["supportPdf"]:
        yield make_link("PDF")
    if payload["supportEpub"]:
        yield make_link("EPUB")
    if payload["supportMobi"]:
        yield make_link("MOBI")


def set_token():
    if not os.path.exists(token_path):
        return
    with open(token_path, "r") as fp:
        token = json.load(fp)
        session.headers["Authorization"] = "Bearer %s" % token


def extract_book_item(item: Dict):
    return item["id"]


def make_extract_book_item(kind: str):
    return lambda item: {
        "id": "%(id)05d" % item,
        "name": item["name"].strip(),
        "kind": kind,
    }


def report():
    shelf_books = map(make_extract_book_item("shelf"), get_book_shelf())
    favourite_books = map(make_extract_book_item("favourite"), get_favourite())
    writer = csv.DictWriter(sys.stdout, ["id", "name", "kind"])
    writer.writeheader()
    writer.writerows(sorted(shelf_books, key=extract_book_item))
    writer.writerows(sorted(favourite_books, key=extract_book_item))


def get_book_flags(payload: Dict):
    if payload["presale"]:
        yield "pre-sale"
    if payload["canSalePaper"]:
        yield "paper"
    if payload["supportPdf"]:
        yield "pdf"
    if payload["supportEpub"]:
        yield "epub"
    if payload["supportMobi"]:
        yield "mobi"
    if payload["supportPushMobi"]:
        yield "push-mobi"


def all_books():
    field_names = ["id", "name", "published", "flags"]
    writer = csv.DictWriter(sys.stdout, field_names)
    writer.writeheader()
    book_id = 1
    failed_count = 0
    while True:
        if failed_count > 1000:
            break
        payload = get_book(book_id)
        book_id += 1
        if payload is None:
            print("# ignored #%s" % book_id, file=sys.stderr)
            failed_count += 1
            continue
        else:
            failed_count = 0
        if payload["publishDate"]:
            payload["publishDate"] = payload["publishDate"][:10]
        item = {
            "id": payload["id"],
            "name": payload["name"].strip(),
            "published": payload["publishDate"],
            "flags": ", ".join(get_book_flags(payload)),
        }
        writer.writerow(item)
        sys.stdout.flush()


def push_books():
    for book in get_book_shelf():
        payload = get_book(book["id"])
        mode = "PushBook" if payload["tupubBookId"] else "PushMiniBook"
        link = urljoin(prefix_mainly, "Kindle/%s/%s" % (mode, book["id"]))

        auth = "Authorization: %(Authorization)s" % session.headers
        print(list2cmdline(["echo", "%(id)05d Push book" % book]))
        print(list2cmdline(["curl", "-H", auth, link]))
        print(list2cmdline(["echo"]))
        sys.stdout.flush()


def clean_favourite():
    shelf_books = set(map(extract_book_item, get_book_shelf()))
    favourite_books = set(map(extract_book_item, get_favourite()))
    purchased_items = shelf_books & favourite_books

    for book_id in sorted(purchased_items):
        link = urljoin(prefix, "Book/UnFav")
        session.post(link, params={"id": book_id})
        print("Unfavourite purchased book: %s" % book_id)


def fetch():
    book_ids = map(extract_book_item, get_book_shelf())
    links = (
        (book_id, link, filename)
        for book in map(download_book, sorted(book_ids))
        for book_id, link, filename in book
    )
    for book_id, link, filename in links:
        options = [
            link,
            'header="Authorization\: %(Authorization)s"' % session.headers,
            "referer=https://m.ituring.com.cn/book/%s" % book_id,
            "out=ebooks/%s" % filename,
        ]
        print("\n\t".join(options))


def login():
    email = input("Email: ")
    password = input("Password: ")
    response = requests.post(
        urljoin(prefix, "Account/Token"), json={"email": email, "password": password}
    )
    payload = response.json()
    if response.status_code != 200:
        print(payload["message"], file=sys.stderr)
        return
    with open(token_path, "w") as fp:
        json.dump(payload["accessToken"], fp)
    print("login done")


def main():
    set_token()

    parser = ArgumentParser(description="ituring helper")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("login")
    subparsers.add_parser("report")
    subparsers.add_parser("fetch")
    subparsers.add_parser("clean-favourite")
    subparsers.add_parser("all-books")
    subparsers.add_parser("push-books")

    args = parser.parse_args()
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(-1)

    actions = {
        "login": login,
        "report": report,
        "fetch": fetch,
        "clean-favourite": clean_favourite,
        "all-books": all_books,
        "push-books": push_books,
    }

    actions[args.action]()


if __name__ == "__main__":
    main()
