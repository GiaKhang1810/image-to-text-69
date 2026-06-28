from flask import Flask, Response, cli, render_template, request

from util import create_log


log = create_log(name=__name__)

cli.show_server_banner = lambda *args, **kwargs: None
server = Flask(import_name=__name__)


@server.after_request
def aft(response: Response) -> Response:
    log.info(
        f"{request.method} {request.remote_addr}: {request.path} {response.status_code}"
    )

    return response


@server.route("/image-to-text", methods=["POST"])
def uploadfile() -> str:
    pass


@server.route("/", methods=["GET"])
def main() -> str:
    return render_template("main.html")


if __name__ == "__main__":
    print("=" * 30)
    log.info(msg="Port: 8080")
    log.info(msg="Host: 127.0.0.1")
    log.info(msg="URL: http://127.0.0.1:8080")
    print("=" * 30)

    server.run(
        host="127.0.0.1",
        port=8080,
        debug=False,
    )
