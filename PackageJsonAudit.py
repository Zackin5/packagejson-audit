# Python script to build a dependency graph of package-lock.json includes

import argparse, pathlib, os, os.path, json, sqlite3
import graphviz

###########
## Logic ##
###########

def load_args():
    """
    Args parsing function
    """
    args = argparse.ArgumentParser()
    args.add_argument('packageDir', metavar='Input package directory', action='store', type=str, help='Path to package directory containing npm package.json and package-lock.json files')
    args.add_argument('outputPath', metavar='Output SQLite file', action='store', type=str, help='Output path for final SQLite DB file')
    args.add_argument('-gv', dest='graphvizOutputPath', metavar='Graphviz output path', action='store', type=str, help='Write path for graphviz output')

    return args.parse_args()

########################
## Database functions ##
########################

def init_database(output_path: str) -> sqlite3.Connection:
    """
    Initialize SQLite database
    """
    absolute_path = os.path.abspath(output_path)

    # Remove existint database file for overwrite
    if os.path.exists(absolute_path):
        os.remove(absolute_path)

    # Create directory path if non existant
    directory_path = os.path.dirname(absolute_path)
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)

    # Initialize SQLite database file and schema
    connection = sqlite3.connect(absolute_path)

    cur = connection.cursor()

    cur.execute('CREATE TABLE packages (id integer PRIMARY KEY NOT NULL, name text NOT NULL, version text NOT NULL, file text NOT NULL, fileSection text NOT NULL, UNIQUE(name, version, fileSection))')
    cur.execute('''CREATE TABLE dependencies 
    (parentName text, parentVersion text, childName text, childVersion text, 
    FOREIGN KEY(parentName) REFERENCES package(name), 
    FOREIGN KEY(parentVersion) REFERENCES package(version), 
    FOREIGN KEY(childName) REFERENCES package(name), 
    FOREIGN KEY(childVersion) REFERENCES package(version),
    UNIQUE(parentName, parentVersion, childName, childVersion))''')

    connection.commit()

    return connection

def database_add_package(db_cursor: sqlite3.Cursor, package_name: str, package_version: str, file: str, file_section: str):
    """
    Insert package information into database
    """
    db_cursor.execute('INSERT OR IGNORE INTO packages (name, version, file, fileSection) VALUES (?, ?, ?, ?)', (package_name, package_version, file, file_section))

def database_add_dependency(db_cursor: sqlite3.Cursor, parent_package_name: str, parent_package_version: str, child_package_name: str, child_package_version: str):
    """
    Add dependency information to database
    """
    db_cursor.execute('INSERT OR IGNORE INTO dependencies VALUES (?, ?, ?, ?)', (parent_package_name, parent_package_version, child_package_name, child_package_version))

###########################
## JSON import functions ##
###########################

def import_package_dependencies(db_cursor: sqlite3.Cursor, package_name: str, package_version: str, package_json_node, source_file: str, file_section: str):
    """
    Imports `requires` and `dependencies` fields of provided json object into database
    """

    if 'dependencies' in package_json_node:
        for dependency_name in package_json_node['dependencies']:
            dependency_json = package_json_node['dependencies'][dependency_name]
            dependency_version = dependency_json['version']

            database_add_package(db_cursor, dependency_name, dependency_version, source_file, file_section)
            database_add_dependency(db_cursor, package_name, package_version, dependency_name, dependency_version)

            import_package_dependencies(db_cursor, dependency_name, dependency_version, dependency_json, source_file, file_section)

    if 'requires' in package_json_node:
        for requirement_name in package_json_node['requires']:
            requirement_version = package_json_node['requires'][requirement_name]

            database_add_package(db_cursor, requirement_name, requirement_version, source_file, file_section)
            database_add_dependency(db_cursor, package_name, package_version, requirement_name, requirement_version)

def import_json_dependency_section(db_cursor: sqlite3.Cursor, package_json, filename: str, section_name: str):
    """
    Imports named section of a json file into the database
    """
    if section_name not in package_json:
        print(f'Section \"{section_name}\" not found in package, skipping')
        return

    for dependency_name in package_json[section_name]:
        dependency_json = package_json[section_name][dependency_name]
        dependency_version: str

        if 'version' in dependency_json:
            dependency_version = dependency_json['version'] # The section we're in uses dependency models
        else:
            dependency_version = dependency_json # The section we're in just uses simple dependency: version references

        database_add_package(db_cursor, dependency_name, dependency_version, filename, section_name)
        import_package_dependencies(db_cursor, dependency_name, dependency_version, dependency_json, filename, section_name)

def parse_package_files(package_dir: str, db_cursor: sqlite3.Cursor):
    """
    Package.json and Package-lock.json parsing logic
    """

    # Verify package files exist
    package_path = os.path.abspath(os.path.join(package_dir,'package.json'))
    package_file = pathlib.Path(package_path)
    if not package_file.exists():
        raise FileNotFoundError(f"Failed to locate package configuration file {package_path}")

    package_lock_path = os.path.abspath(os.path.join(package_dir,'package-lock.json'))
    package_lock_file = pathlib.Path(package_lock_path)
    if not package_lock_file.exists():
        raise FileNotFoundError(f"Failed to locate package lock file {package_lock_path}")

    # Load package file JSON
    package_json = json.load(package_file.open())
    package_lock_json = json.load(package_lock_file.open())

     # Get package.json dependencies
    import_json_dependency_section(db_cursor, package_json, 'package.json', 'dependencies')
    import_json_dependency_section(db_cursor, package_json, 'package.json', 'devDependencies')

     # Get package-lock.json dependencies
    import_json_dependency_section(db_cursor, package_lock_json, 'package-lock.json', 'dependencies')


########################
## GraphViz functions ##
########################

def escape_graphviz_str(input_string: str) -> str:
    """
    Replaces special characters in a string with a GraphViz friendly value
    """

    for i, c in enumerate(input_string):
        if c == '-':
            input_string = input_string[:i] + '_' + input_string[i+1:]
        if c == '@':
            input_string = input_string[:i] + 'a' + input_string[i+1:]
        if c == '/':
            input_string = input_string[:i] + 'f' + input_string[i+1:]
        if c == '.':
            input_string = input_string[:i] + 'p' + input_string[i+1:]

    return input_string

def output_graphviz(graphviz_output_path: str, db_cursor: sqlite3.Cursor):
    """
    Function to generate GraphViz output from database contents
    """
    dot_string = '''digraph package_dependency_graph {
    node [shape=record];
    rankdir=LR;\n'''

    # Populate nodes
    dot_string += '\tsubgraph cluster_package_json {\n'
    dot_string += '\t\tlabel="package.json";\n'
    for package in db_cursor.execute('SELECT name, GROUP_CONCAT("<p" || id || "> " || REPLACE(REPLACE(version, ">", "\>"), "<", "\<"), " | ") ' +
            'FROM packages ' +
            'WHERE file = "package.json" ' +
            'GROUP BY name'):
        package_name = package[0]
        package_versions = package[1]
        dot_string += f'\t\t{escape_graphviz_str(package_name)} [label="{package_name} | {package_versions}"];\n'
    dot_string += '\t}\n'

    dot_string += '\tsubgraph cluster_lock_json {\n'
    dot_string += '\t\tlabel="package-lock.json";\n'
    for package in db_cursor.execute('SELECT name, GROUP_CONCAT("<p" || id || "> " || REPLACE(REPLACE(version, ">", "\>"), "<", "\<"), " | ") ' +
            'FROM packages ' +
            'WHERE file = "package-lock.json" ' +
            'GROUP BY name'):
        package_name = package[0]
        package_versions = package[1]
        dot_string += f'\t\t{escape_graphviz_str(package_name)} [label="{package_name} | {package_versions}"];\n'
    dot_string += '\t}\n'

    # Populate edges
    for dependency in db_cursor.execute('SELECT "<p" || parent.id || ">", parentName, "<p" || child.id || ">", childName\n' +
            'FROM dependencies\n' +
            'JOIN packages AS parent ON\n' +
                '\tdependencies.parentName == parent.name and\n' +
                '\tdependencies.parentVersion == parent.version\n' +
            'JOIN packages AS child ON\n' +
                '\tdependencies.childName == child.name and\n' +
                '\tdependencies.childVersion == child.version'):
        parent_id = dependency[0]
        parent_node = escape_graphviz_str(dependency[1])
        child_id = dependency[2]
        child_node = escape_graphviz_str(dependency[3])

        dot_string += f'\t"{child_node}":{child_id} -> "{parent_node}":{parent_id};\n'

    dot_string += "}"

    # Render graphviz
    dot = graphviz.Source(dot_string)

    if dot is None:
        raise Exception(f'Invalid GraphViz output generated:\n"{dot_string}"')

    dot.render(engine='dot', format='svg', outfile=graphviz_output_path)


####################
## Entry function ##
####################

def main():
    """
    Entry function
    """
    args = load_args()

    print('Parsing package files...')
    db = init_database(args.outputPath)
    db_cursor = db.cursor()
    parse_package_files(args.packageDir, db_cursor)
    db.commit()

    if args.graphvizOutputPath is not None:
        print('Building GraphViz graph...')
        output_graphviz(args.graphvizOutputPath, db_cursor)

    db.close()

    print('Complete')

if __name__ == "__main__":
    main()