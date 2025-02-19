#!/usr/bin/env bash

set -o pipefail

AZ_LOGIN_ARGS=$(eval "echo $AZ_CLI_LOGIN_ARGS")

# Login as spn
az login --only-show-errors ${AZ_LOGIN_ARGS}

# cd into function app dir
cd ${DEPLOY_FROM_WORKING_DIR}
#ls -la 

# Deploy via azure function tools
func azure functionapp publish ${FUNC_NAME} --subscription ${SUBSCRIPTION_ID} --python