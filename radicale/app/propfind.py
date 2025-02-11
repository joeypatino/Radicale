# This file is part of Radicale Server - Calendar Server
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
# Copyright © 2008-2017 Guillaume Ayoub
# Copyright © 2017-2018 Unrud <unrud@outlook.com>
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Radicale.  If not, see <http://www.gnu.org/licenses/>.

import collections
import itertools
import posixpath
import socket
import xml.etree.ElementTree as ET
from http import client

from radicale import app, httputils, pathutils, rights, storage, xmlutils
from radicale.log import logger


def xml_propfind(base_prefix, path, xml_request, allowed_items, user,
                 encoding):
    """Read and answer PROPFIND requests.

    Read rfc4918-9.1 for info.

    The collections parameter is a list of collections that are to be included
    in the output.

    """
    # A client may choose not to submit a request body.  An empty PROPFIND
    # request body MUST be treated as if it were an 'allprop' request.
    top_element = (xml_request[0] if xml_request is not None else
                   ET.Element(xmlutils.make_clark("D:allprop")))

    props = ()
    allprop = False
    propname = False
    if top_element.tag == xmlutils.make_clark("D:allprop"):
        allprop = True
    elif top_element.tag == xmlutils.make_clark("D:propname"):
        propname = True
    elif top_element.tag == xmlutils.make_clark("D:prop"):
        props = [prop.tag for prop in top_element]

    if xmlutils.make_clark("D:current-user-principal") in props and not user:
        # Ask for authentication
        # Returning the DAV:unauthenticated pseudo-principal as specified in
        # RFC 5397 doesn't seem to work with DAVx5.
        return client.FORBIDDEN, None

    # Writing answer
    multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))

    for item, permission in allowed_items:
        write = permission == "w"
        multistatus.append(xml_propfind_response(
            base_prefix, path, item, props, user, encoding, write=write,
            allprop=allprop, propname=propname))

    return client.MULTI_STATUS, multistatus


def xml_propfind_response(base_prefix, path, item, props, user, encoding,
                          write=False, propname=False, allprop=False):
    """Build and return a PROPFIND response."""
    if propname and allprop or (props and (propname or allprop)):
        raise ValueError("Only use one of props, propname and allprops")
    is_collection = isinstance(item, storage.BaseCollection)
    if is_collection:
        is_leaf = item.get_meta("tag") in ("VADDRESSBOOK", "VCALENDAR")
        collection = item
    else:
        collection = item.collection

    response = ET.Element(xmlutils.make_clark("D:response"))
    href = ET.Element(xmlutils.make_clark("D:href"))
    if is_collection:
        # Some clients expect collections to end with /
        uri = pathutils.unstrip_path(item.path, True)
    else:
        uri = pathutils.unstrip_path(
            posixpath.join(collection.path, item.href))
    href.text = xmlutils.make_href(base_prefix, uri)
    response.append(href)

    if propname or allprop:
        props = []
        # Should list all properties that can be retrieved by the code below
        props.append(xmlutils.make_clark("D:principal-collection-set"))
        props.append(xmlutils.make_clark("D:current-user-principal"))
        props.append(xmlutils.make_clark("D:current-user-privilege-set"))
        props.append(xmlutils.make_clark("D:supported-report-set"))
        props.append(xmlutils.make_clark("D:resourcetype"))
        props.append(xmlutils.make_clark("D:owner"))

        if is_collection and collection.is_principal:
            props.append(xmlutils.make_clark("C:calendar-user-address-set"))
            props.append(xmlutils.make_clark("D:principal-URL"))
            props.append(xmlutils.make_clark("CR:addressbook-home-set"))
            props.append(xmlutils.make_clark("C:calendar-home-set"))

        if not is_collection or is_leaf:
            props.append(xmlutils.make_clark("D:getetag"))
            props.append(xmlutils.make_clark("D:getlastmodified"))
            props.append(xmlutils.make_clark("D:getcontenttype"))
            props.append(xmlutils.make_clark("D:getcontentlength"))

        if is_collection:
            if is_leaf:
                props.append(xmlutils.make_clark("D:displayname"))
                props.append(xmlutils.make_clark("D:sync-token"))
            if collection.get_meta("tag") == "VCALENDAR":
                props.append(xmlutils.make_clark("CS:getctag"))
                props.append(
                    xmlutils.make_clark("C:supported-calendar-component-set"))

            meta = item.get_meta()
            for tag in meta:
                if tag == "tag":
                    continue
                clark_tag = xmlutils.make_clark(tag)
                if clark_tag not in props:
                    props.append(clark_tag)

    responses = collections.defaultdict(list)
    if propname:
        for tag in props:
            responses[200].append(ET.Element(tag))
        props = ()
    for tag in props:
        element = ET.Element(tag)
        is404 = False
        if tag == xmlutils.make_clark("D:getetag"):
            if not is_collection or is_leaf:
                element.text = item.etag
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:getlastmodified"):
            if not is_collection or is_leaf:
                element.text = item.last_modified
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:principal-collection-set"):
            child_element = ET.Element(xmlutils.make_clark("D:href"))
            child_element.text = xmlutils.make_href(base_prefix, "/")
            element.append(child_element)
        elif (tag in (xmlutils.make_clark("C:calendar-user-address-set"),
                      xmlutils.make_clark("D:principal-URL"),
                      xmlutils.make_clark("CR:addressbook-home-set"),
                      xmlutils.make_clark("C:calendar-home-set")) and
              collection.is_principal and is_collection):
            child_element = ET.Element(xmlutils.make_clark("D:href"))
            child_element.text = xmlutils.make_href(base_prefix, path)
            element.append(child_element)
        elif tag == xmlutils.make_clark("C:supported-calendar-component-set"):
            human_tag = xmlutils.make_human_tag(tag)
            if is_collection and is_leaf:
                meta = item.get_meta(human_tag)
                if meta:
                    components = meta.split(",")
                else:
                    components = ("VTODO", "VEVENT", "VJOURNAL")
                for component in components:
                    comp = ET.Element(xmlutils.make_clark("C:comp"))
                    comp.set("name", component)
                    element.append(comp)
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:current-user-principal"):
            if user:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(
                    base_prefix, "/%s/" % user)
                element.append(child_element)
            else:
                element.append(ET.Element(
                    xmlutils.make_clark("D:unauthenticated")))
        elif tag == xmlutils.make_clark("D:current-user-privilege-set"):
            privileges = ["D:read"]
            if write:
                privileges.append("D:all")
                privileges.append("D:write")
                privileges.append("D:write-properties")
                privileges.append("D:write-content")
            for human_tag in privileges:
                privilege = ET.Element(xmlutils.make_clark("D:privilege"))
                privilege.append(ET.Element(
                    xmlutils.make_clark(human_tag)))
                element.append(privilege)
        elif tag == xmlutils.make_clark("D:supported-report-set"):
            # These 3 reports are not implemented
            reports = ["D:expand-property",
                       "D:principal-search-property-set",
                       "D:principal-property-search"]
            if is_collection and is_leaf:
                reports.append("D:sync-collection")
                if item.get_meta("tag") == "VADDRESSBOOK":
                    reports.append("CR:addressbook-multiget")
                    reports.append("CR:addressbook-query")
                elif item.get_meta("tag") == "VCALENDAR":
                    reports.append("C:calendar-multiget")
                    reports.append("C:calendar-query")
            for human_tag in reports:
                supported_report = ET.Element(
                    xmlutils.make_clark("D:supported-report"))
                report_element = ET.Element(xmlutils.make_clark("D:report"))
                report_element.append(
                    ET.Element(xmlutils.make_clark(human_tag)))
                supported_report.append(report_element)
                element.append(supported_report)
        elif tag == xmlutils.make_clark("D:getcontentlength"):
            if not is_collection or is_leaf:
                element.text = str(len(item.serialize().encode(encoding)))
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:owner"):
            # return empty elment, if no owner available (rfc3744-5.1)
            if collection.owner:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(
                    base_prefix, "/%s/" % collection.owner)
                element.append(child_element)
        elif is_collection:
            if tag == xmlutils.make_clark("D:getcontenttype"):
                if is_leaf:
                    element.text = xmlutils.MIMETYPES[item.get_meta("tag")]
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:resourcetype"):
                if item.is_principal:
                    child_element = ET.Element(
                        xmlutils.make_clark("D:principal"))
                    element.append(child_element)
                if is_leaf:
                    if item.get_meta("tag") == "VADDRESSBOOK":
                        child_element = ET.Element(
                            xmlutils.make_clark("CR:addressbook"))
                        element.append(child_element)
                    elif item.get_meta("tag") == "VCALENDAR":
                        child_element = ET.Element(
                            xmlutils.make_clark("C:calendar"))
                        element.append(child_element)
                child_element = ET.Element(xmlutils.make_clark("D:collection"))
                element.append(child_element)
            elif tag == xmlutils.make_clark("RADICALE:displayname"):
                # Only for internal use by the web interface
                displayname = item.get_meta("D:displayname")
                if displayname is not None:
                    element.text = displayname
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:displayname"):
                displayname = item.get_meta("D:displayname")
                if not displayname and is_leaf:
                    displayname = item.path
                if displayname is not None:
                    element.text = displayname
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:getctag"):
                if is_leaf:
                    element.text = item.etag
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:sync-token"):
                if is_leaf:
                    element.text, _ = item.sync()
                else:
                    is404 = True
            else:
                human_tag = xmlutils.make_human_tag(tag)
                meta = item.get_meta(human_tag)
                if meta is not None:
                    element.text = meta
                else:
                    is404 = True
        # Not for collections
        elif tag == xmlutils.make_clark("D:getcontenttype"):
            element.text = xmlutils.get_content_type(item, encoding)
        elif tag == xmlutils.make_clark("D:resourcetype"):
            # resourcetype must be returned empty for non-collection elements
            pass
        else:
            is404 = True

        responses[404 if is404 else 200].append(element)

    for status_code, childs in responses.items():
        if not childs:
            continue
        propstat = ET.Element(xmlutils.make_clark("D:propstat"))
        response.append(propstat)
        prop = ET.Element(xmlutils.make_clark("D:prop"))
        prop.extend(childs)
        propstat.append(prop)
        status = ET.Element(xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(status_code)
        propstat.append(status)

    return response


class ApplicationPropfindMixin:
    def _collect_allowed_items(self, items, user):
        """Get items from request that user is allowed to access."""
        for item in items:
            if isinstance(item, storage.BaseCollection):
                path = pathutils.unstrip_path(item.path, True)
                if item.get_meta("tag"):
                    permissions = rights.intersect(
                        self._rights.authorization(user, path), "rw")
                    target = "collection with tag %r" % item.path
                else:
                    permissions = rights.intersect(
                        self._rights.authorization(user, path), "RW")
                    target = "collection %r" % item.path
            else:
                path = pathutils.unstrip_path(item.collection.path, True)
                permissions = rights.intersect(
                    self._rights.authorization(user, path), "rw")
                target = "item %r from %r" % (item.href, item.collection.path)
            if rights.intersect(permissions, "Ww"):
                permission = "w"
                status = "write"
            elif rights.intersect(permissions, "Rr"):
                permission = "r"
                status = "read"
            else:
                permission = ""
                status = "NO"
            logger.debug(
                "%s has %s access to %s",
                repr(user) if user else "anonymous user", status, target)
            if permission:
                yield item, permission

    def do_PROPFIND(self, environ, base_prefix, path, user, context=None):
        """Manage PROPFIND request."""
        access = app.Access(self._rights, user, path)
        if not access.check("r"):
            return httputils.NOT_ALLOWED
        try:
            xml_content = self._read_xml_request_body(environ)
        except RuntimeError as e:
            logger.warning(
                "Bad PROPFIND request on %r: %s", path, e, exc_info=True)
            return httputils.BAD_REQUEST
        except socket.timeout:
            logger.debug("Client timed out", exc_info=True)
            return httputils.REQUEST_TIMEOUT
        with self._storage.acquire_lock("r", user):
            items = self._storage.discover(
                path, environ.get("HTTP_DEPTH", "0"))
            # take root item for rights checking
            item = next(items, None)
            if not item:
                return httputils.NOT_FOUND
            if not access.check("r", item):
                return httputils.NOT_ALLOWED
            # put item back
            items = itertools.chain([item], items)
            allowed_items = self._collect_allowed_items(items, user)
            headers = {"DAV": httputils.DAV_HEADERS,
                       "Content-Type": "text/xml; charset=%s" % self._encoding}
            status, xml_answer = xml_propfind(
                base_prefix, path, xml_content, allowed_items, user,
                self._encoding)
            if status == client.FORBIDDEN and xml_answer is None:
                return httputils.NOT_ALLOWED
            return status, headers, self._xml_response(xml_answer)
