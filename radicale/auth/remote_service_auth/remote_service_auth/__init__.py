from radicale.auth import BaseAuth
from radicale.log import logger
import requests

# This is an example of how to implement authentication against a remote server.
# here we are telling radical to use the `authorization_endpoint` found under the `auth`
# config setting section, and make a POST http call with the `userName` and `password` values

PLUGIN_CONFIG_SCHEMA = {"auth":
                            {"authorization_endpoint": {"value": "", "type": str},
                             "username_field": {"value": "", "type": str},
                             "password_field": {"value": "", "type": str}},
                        }


class Auth(BaseAuth):
    def __init__(self, configuration):
        super().__init__(configuration.copy(PLUGIN_CONFIG_SCHEMA))

    def login(self, login, password):
        url = self.configuration.get("auth", "authorization_endpoint")
        usernameField = self.configuration.get("auth", "username_field")
        passwordField = self.configuration.get("auth", "password_field")
        response = requests.post(url, data={usernameField: login, passwordField: password})
        data = response.json()

        if data["status"] != 200:
            logger.info("response code is not 200")
            return ("", None)

        result = data["result"]
        if result is None:
            logger.info("invalid response")
            return ("", None)

        userId = result["user"]["id"]
        if userId is not None:
            return (str(userId), str(result))
        else:
            logger.info("invalid response. no userId")
            return ("", None)
