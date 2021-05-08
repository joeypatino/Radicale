from radicale import auth
from radicale.log import logger
import requests

PLUGIN_CONFIG_SCHEMA = {"auth":
                            {"authorization_endpoint": {"value": "", "type": str},
                             "username_field": {"value": "", "type": str},
                             "password_field": {"value": "", "type": str}},
                        }

class Auth(auth.BaseAuth):
    def __init__(self, configuration):
        super().__init__(configuration.copy(PLUGIN_CONFIG_SCHEMA))

    def login(self, login, password):
        url = self.configuration.get("auth", "authorization_endpoint")
        usernameField = self.configuration.get("auth", "username_field")
        passwordField = self.configuration.get("auth", "password_field")
        response = requests.post(url, data={usernameField: login, passwordField: password})
        data = response.json()

        if response.status_code != 200:
            logger.info("response code is not 200")
            return ""

        result = data["user"]
        if result is None:
            logger.info("invalid response")
            return ""
        else:
            return str(result)